"""Repl: an ephemeral code cell that execs against a shared namespace.

For quick inspection and control from the canvas (e.g. ``print(canvas.servo.x)``)
-- not a source-of-truth notebook. The cell runs on the canvas :class:`Kernel`
thread so a slow statement can't freeze the UI, and its output (stdout, stderr,
and the repr of a trailing expression) is shown below the editor.

The namespace is supplied by :meth:`Canvas.enable_repl`; call it before
inserting a ``Repl``. Cell text is not persisted across serves by design.
"""

import builtins
import keyword
import re

from .base import BaseComponent

# Attribute completion evaluates the expression before the final dot to dir() it.
# Restrict that to plain dotted-name chains (no calls or subscripts) so typing
# never triggers side effects -- you don't want `foo().` to call `foo()`.
_DOTTED_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _complete_token(text, ns):
    """Completion candidates for the partial token ``text`` against ``ns``.

    ``text`` is the identifier-or-attribute-chain just before the cursor, e.g.
    ``"canv"`` or ``"canvas.serv"``. Returns the candidate *final segments*
    (global or attribute names), not the full dotted path -- the editor replaces
    only the part after the last dot. Dunder names are hidden unless the partial
    already starts with an underscore.
    """
    if "." in text:
        expr, _, partial = text.rpartition(".")
        if not expr or not _DOTTED_NAME.match(expr):
            return []
        try:
            obj = eval(expr, dict(ns))  # noqa: S307 - a REPL; eval is the point
        except Exception:
            return []
        names = dir(obj)
    else:
        partial = text
        names = list(ns.keys()) + dir(builtins) + keyword.kwlist
    show_dunder = partial.startswith("_")
    out = {
        n for n in names
        if n.startswith(partial) and (show_dunder or not n.startswith("__"))
    }
    return sorted(out)[:200]


class Repl(BaseComponent):
    component = "Repl"
    default_w = 460
    default_h = 260

    def __init__(self, label="repl"):
        super().__init__(label=label, code="", output="", result="")
        # Both injected by Canvas.insert (see its wiring).
        self._kernel = None
        self._namespace = None

    def register_props(self):
        return dict(self._props)  # label, w, h, code, output, result

    def _handle_input(self, payload):
        if payload.get("action") == "complete":
            self._complete(payload.get("reqId"), payload.get("text", ""))
            return
        code = payload.get("code")
        if code is None or self._kernel is None:
            return
        self._props["code"] = code
        ns = self._namespace if self._namespace is not None else {}

        def job():
            from ..kernel import run_code

            output, result = run_code(code, ns)
            self._props["output"] = output
            self._props["result"] = result or ""
            self._send_update({"output": output, "result": result or ""})

        self._kernel.submit(job)

    def _complete(self, req_id, text):
        """Answer an editor autocomplete request from the shared namespace.

        Computed inline (not on the kernel thread) so completions stay snappy
        even while a cell is running; the candidate list is matched against the
        live REPL namespace, builtins and keywords. The reply is a dedicated
        ``complete_result`` message correlated by ``reqId`` -- it bypasses shape
        props so it never touches the canvas store or undo history.
        """
        if req_id is None or self._bridge is None:
            return
        ns = self._namespace if self._namespace is not None else {}
        try:
            completions = _complete_token(text, ns)
        except Exception:
            completions = []
        self._bridge.broadcast({
            "type": "complete_result",
            "reqId": req_id,
            "completions": completions,
        })
