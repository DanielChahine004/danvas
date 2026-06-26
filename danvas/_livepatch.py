"""Body-only live function patching — the smart middle tier of hot reload.

When a save changes *only the bodies of top-level functions* (event handlers, or
the helpers those handlers call), the running worker can swap those functions'
code objects in place instead of restarting the whole process. Nothing is
executed at swap time: a handler simply runs the new bytecode the next time it
fires, so the heap, open sockets, daemon threads, slider positions, and an
in-memory ML model all survive untouched.

Anything outside that narrow, provably-safe class — a changed import, a new or
moved global, a signature/decorator change, an added or removed function, an
edit to a running ``@canvas.background`` worker — makes :func:`safe_live_diff`
(or :func:`apply_live_patch`) decline, and the monitor falls back to a full
restart. The classifier is conservative on purpose: a false "restart" only costs
a restart, while a false "safe" would corrupt the live process, so it only ever
says "safe" when it can prove it.

Used from both sides of ``serve(hot_reload=True)``: the monitor's
:func:`danvas.hotreload._apply_live_patch` POSTs the old + new script text to the
worker, whose ``/__hot_patch__`` endpoint (see :func:`danvas.server.create_app`)
calls :func:`safe_live_diff` then :func:`apply_live_patch` to perform the swap.

The governing rule, which predicts every case: code that is *re-entered after*
the swap (an event handler, a per-iteration callback a dumb loop calls) is
swappable; code whose frame is parked *right now* (the module top level, or a
running worker loop) is not — for those, a restart is the correct answer.
"""

import ast
import copy
import os
import types


def _top_level_funcs(tree):
    """The module-level ``def`` / ``async def`` nodes, in source order."""
    return [n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def safe_live_diff(old_text, new_text):
    """Classify the diff between two script versions for in-process patching.

    Returns a list of ``{"name": str, "occ": int}`` identifying the top-level
    functions whose *bodies* changed — when that is the only difference between
    the two scripts. ``occ`` is the 0-based index of the function among
    same-named top-level defs in source order, so the common ``def _`` handler
    idiom (several top-level functions all named ``_``) stays unambiguous.

    Returns ``None`` when a full restart is needed: a syntax error, or any change
    outside a top-level function body — an import, a module-level assignment, a
    changed/added/removed signature or decorator, or a new or deleted function.

    Returns ``[]`` when the two scripts parse to the identical AST (only comments
    or whitespace differ), which the caller can treat as a no-op.
    """
    try:
        old_tree = ast.parse(old_text)
        new_tree = ast.parse(new_text)
    except SyntaxError:
        return None

    # Everything *except* top-level function bodies must match exactly. Blank
    # each top-level function's body to a single ``pass`` (keeping its name,
    # args and decorators) and compare the whole module: any remaining
    # difference is an import, a global, a signature/decorator edit, or an
    # added/removed def — all of which need a restart. ``ast.dump`` omits line
    # numbers by default, so a body edit that shifts later lines doesn't trip it.
    def _blanked(tree):
        t = copy.deepcopy(tree)
        for node in t.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node.body = [ast.Pass()]
        return ast.dump(t)

    if _blanked(old_tree) != _blanked(new_tree):
        return None

    old_funcs = _top_level_funcs(old_tree)
    new_funcs = _top_level_funcs(new_tree)

    # Reject a module-level *nested* def (inside an if/for/try/with at top level)
    # that shadows a top-level function name: it would also land in the module's
    # code consts and make per-name occurrence indexing ambiguous. Rare, and a
    # restart handles it correctly.
    top_names = {n.name for n in new_funcs}
    for node in new_tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and sub.name in top_names):
                return None

    # Structure matches, so the two scripts have the same top-level functions in
    # the same order. Report the ones whose body actually changed.
    specs = []
    counts = {}
    for old_fn, new_fn in zip(old_funcs, new_funcs):
        name = new_fn.name
        occ = counts.get(name, 0)
        counts[name] = occ + 1
        if ast.dump(old_fn) != ast.dump(new_fn):
            specs.append({"name": name, "occ": occ})
    return specs


def _func_codes(text, filename):
    """``{(co_name, occ): code}`` for every top-level function in ``text``.

    ``occ`` orders same-named top-level code objects by their first line, which
    matches the source order :func:`safe_live_diff` counts in. No execution —
    :func:`compile` only produces the code objects; the function bodies never run.
    """
    module_code = compile(text, filename, "exec")
    consts = sorted(
        (c for c in module_code.co_consts if isinstance(c, types.CodeType)),
        key=lambda c: c.co_firstlineno,
    )
    out = {}
    counts = {}
    for c in consts:
        occ = counts.get(c.co_name, 0)
        counts[c.co_name] = occ + 1
        out[(c.co_name, occ)] = c
    return out


def _unwrap(fn):
    """Follow ``__wrapped__`` to the user's function under a ``functools.wraps``
    shim (danvas wraps only un-attributable callables — see ``_mark_threaded``)."""
    seen = set()
    while isinstance(fn, types.FunctionType) and hasattr(fn, "__wrapped__"):
        if id(fn) in seen:
            break
        seen.add(id(fn))
        nxt = fn.__wrapped__
        if not isinstance(nxt, types.FunctionType):
            break
        fn = nxt
    return fn


def _same_file(co_filename, main_file):
    if co_filename == main_file:
        return True
    try:
        return os.path.abspath(co_filename) == os.path.abspath(main_file)
    except (OSError, ValueError):
        return False


def _live_top_level_funcs(main_module, components):
    """Every top-level function *defined in the script* that's reachable live.

    Pulls from the script's own globals (``__main__``) and from the callback
    stores on each live component — the ``@button.on_click`` handlers, which are
    the same function object the script bound (``_register_callback`` returns
    ``fn`` unwrapped). Filtered to functions defined in the main file and at
    module top level (``__qualname__`` has no ``.``), so imported functions and
    nested closures can never be mistaken for a top-level target. Deduplicated by
    identity."""
    main_file = getattr(main_module, "__file__", None)
    out = {}

    def consider(v):
        if not isinstance(v, types.FunctionType):
            return
        fn = _unwrap(v)
        if not isinstance(fn, types.FunctionType):
            return
        if "." in getattr(fn, "__qualname__", "."):
            return  # a method or a nested/closure function, not module top level
        if main_file is None or not _same_file(fn.__code__.co_filename, main_file):
            return
        out[id(fn)] = fn

    for v in list(vars(main_module).values()):
        consider(v)
    for comp in components:
        for v in list(vars(comp).values()):
            if isinstance(v, types.FunctionType):
                consider(v)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    consider(x)
            elif isinstance(v, dict):
                for x in v.values():
                    if isinstance(x, (list, tuple)):
                        for y in x:
                            consider(y)
                    else:
                        consider(x)
    return list(out.values())


def _fingerprint(code):
    """A strong value-identity key for a function's code, independent of file
    name and line numbers, so a code object recompiled from the *same* source
    matches the one currently live in the worker.

    ``co_consts`` is included so two functions that differ only by a literal
    (``v + 1`` vs ``v + 2``, identical ``co_code`` but different constants) don't
    collide; code constants are always hashable (numbers, strings, tuples,
    nested code objects), so the tuple stays usable as a set key. ``co_name`` is
    included so two identically-bodied functions with *different* names (a
    duplicated helper) aren't treated as the same target."""
    return (code.co_name, code.co_code, code.co_consts, code.co_names,
            code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount)


def apply_live_patch(main_module, components, old_text, new_text, specs,
                     background_funcs=()):
    """Swap the code objects of the changed top-level functions, in place.

    ``specs`` comes from :func:`safe_live_diff`. Returns ``(ok, detail)``:
    ``(True, [name, ...])`` after a clean swap, or ``(False, reason)`` when the
    caller should fall back to a restart. Validation is all-or-nothing — every
    target is resolved and checked before a single ``__code__`` is reassigned —
    so a patch that can't be fully applied leaves the process exactly as it was.

    Declines (→ restart) when a target can't be uniquely located, when a closure
    changes shape (``__code__`` assignment requires a matching free-variable
    count), or when the target is a registered ``@canvas.background`` worker: its
    loop is already parked in the old bytecode, so swapping its code would
    silently do nothing — a restart is what actually re-runs it.
    """
    if not specs:
        return True, []
    main_file = getattr(main_module, "__file__", "<main>")
    try:
        old_codes = _func_codes(old_text, main_file)
        new_codes = _func_codes(new_text, main_file)
    except SyntaxError as exc:
        return False, f"compile failed: {exc}"

    live = _live_top_level_funcs(main_module, components)
    background_prints = {
        _fingerprint(_unwrap(bg).__code__)
        for bg in background_funcs if isinstance(bg, types.FunctionType)
    }

    plan = []
    for spec in specs:
        key = (spec["name"], spec["occ"])
        old_code = old_codes.get(key)
        new_code = new_codes.get(key)
        if old_code is None or new_code is None:
            return False, f"could not resolve function {spec['name']!r}"
        old_print = _fingerprint(old_code)
        if old_print in background_prints:
            return False, (f"{spec['name']!r} is a @canvas.background worker; "
                           "restart to re-run it")
        targets = [f for f in live if _fingerprint(f.__code__) == old_print]
        if len(targets) != 1:
            return False, (f"{spec['name']!r}: {len(targets)} live matches "
                           "(need exactly 1)")
        target = targets[0]
        if len(new_code.co_freevars) != len(target.__code__.co_freevars):
            return False, f"{spec['name']!r}: closure shape changed"
        plan.append((target, new_code))

    # All validated — apply. Each assignment is a single attribute set with no
    # I/O, so the batch is effectively atomic from a handler's point of view.
    for target, new_code in plan:
        target.__code__ = new_code
    return True, [s["name"] for s in specs]
