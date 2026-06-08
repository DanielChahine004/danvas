"""Auto-capture: mirror every notebook cell's output onto the canvas.

Registers an IPython ``post_run_cell`` hook so each cell that ends in an
expression produces (or refreshes) its own panel on the canvas — no manual
wrapping or :meth:`Canvas.insert` per cell. The cell's output value is turned
into a panel by reusing Jupyter's own rich-display machinery
(``DisplayFormatter``), so DataFrames, matplotlib figures, Plotly figures, and
any object with ``_repr_html_`` render the same way they would inline.

Typical use::

    import pycanvas
    canvas = pycanvas.Canvas().serve(block=False)
    pycanvas.autopanel(canvas)        # or: canvas.capture_cells()
    # every subsequent cell mirrors its Out[...] value to the canvas

Re-running a cell swaps its panel in place (keyed on the notebook's stable
cell id) rather than stacking a duplicate. Cells that end in a statement
(an assignment, a ``print``, a loop) produce no output value and are skipped.

A cell may override its own panel with a ``# pycanvas:`` directive line — to
pin a position/size, lock it, rename it, or opt out entirely::

    # pycanvas: x=40 y=80 w=600 h=400 movable=false
    fig

    # pycanvas: name=metrics label="Live metrics" locked=true
    df

    # pycanvas: skip
    secret  # not mirrored to the canvas

Recognised keys: ``x y w h rotation`` (numbers), ``locked movable resizable
interactive`` (true/false), ``name``/``label`` (strings), and a bare ``skip``.
Anything unspecified falls back to the auto-grid (or, on a re-run, to wherever
the user dragged/resized the panel).
"""

import re
import warnings

from .components import Custom, Label, Plot


class CellCapture:
    """Holds the ``post_run_cell`` hook state for one canvas.

    One instance is created per :func:`autopanel` call and stored on the canvas
    as ``canvas._cell_capture`` so a second call is idempotent (it re-uses the
    existing capture instead of registering the hook twice).
    """

    def __init__(self, canvas, cols=3, slot_w=520, slot_h=420, gap=40,
                 origin=(0, 0), include_source=True):
        self.canvas = canvas
        self.cols = cols
        self.slot_w = slot_w
        self.slot_h = slot_h
        self.gap = gap
        self.origin = origin
        self.include_source = include_source
        self._ip = None
        # Maps a cell's stable id -> the grid slot it claimed, so re-running a
        # cell lands its refreshed panel back in the same spot. ``_next_slot`` is
        # the running counter handed to each *new* cell.
        self._slots = {}
        self._next_slot = 0

    # -- (un)registration ----------------------------------------------------
    def start(self):
        """Register the ``post_run_cell`` hook. Raises if not under IPython."""
        from IPython import get_ipython

        ip = get_ipython()
        if ip is None:
            raise RuntimeError(
                "autopanel() needs a running IPython/Jupyter kernel "
                "(get_ipython() returned None)."
            )
        self._ip = ip
        ip.events.register("post_run_cell", self._on_cell)
        return self

    def stop(self):
        """Unregister the hook so later cells stop producing panels."""
        if self._ip is not None:
            try:
                self._ip.events.unregister("post_run_cell", self._on_cell)
            except ValueError:
                pass  # already unregistered
            self._ip = None

    # -- the hook ------------------------------------------------------------
    def _on_cell(self, result):
        """Fired by IPython after every cell. Turns its output into a panel."""
        # Never let a rendering hiccup surface as a cell error — the user's code
        # already ran; this is a best-effort mirror.
        try:
            if not self._is_user_cell(result):
                return  # tooling probe (e.g. VS Code's variable viewer), not a cell
            out = result.result
            if out is None:
                return  # statement cell (assignment/print/loop): nothing to show
            directive = _parse_directive(result.info.raw_cell)
            if directive.get("skip"):
                return  # cell opted out of the canvas with `# pycanvas: skip`
            name = directive.pop("name", None) or self._panel_name(result)
            label = directive.pop("label", None)
            comp = self._build(out, name, result, label=label)
            place = self._placement(result, name, directive)
            # Re-running a cell intentionally replaces its panel. Pull the old one
            # off first so insert() doesn't fire its "name already used" collision
            # warning (that guard is for accidental clashes in the manual API; an
            # autopanel re-run is a deliberate swap). _placement already captured
            # the old panel's live geometry above, so removing it loses nothing.
            prev = self.canvas._named.get(name)
            if prev is not None:
                self.canvas.remove(prev)
            self.canvas.insert(comp, **place)
        except Exception:
            import traceback

            traceback.print_exc()

    @staticmethod
    def _is_user_cell(result):
        """Whether this execution is a real notebook cell the user ran.

        IDE tooling (VS Code's variable viewer / Data Wrangler, completion
        probes, etc.) runs introspection code through the same kernel, firing
        ``post_run_cell`` just like a real cell. Those are flagged
        ``store_history=False`` (and usually ``silent=True``) — they don't get an
        ``In[n]``/``Out[n]`` slot — whereas a cell the user actually executed is
        recorded in history. Mirror only the latter so probe outputs (e.g. the
        ``__DW_SCOPE__[...]`` panels) don't litter the canvas.
        """
        info = result.info
        if getattr(info, "silent", False):
            return False
        # store_history defaults to True when absent (older IPython / odd hosts),
        # so a missing attribute is treated as a genuine user cell.
        return getattr(info, "store_history", True)

    # -- layout --------------------------------------------------------------
    def _placement(self, result, name, directive):
        """Resolve the ``insert`` placement kwargs for a cell's panel.

        Precedence, per field: an explicit ``# pycanvas:`` directive wins; else
        the panel's current live geometry is reused (so a re-run keeps where the
        user dragged/resized/locked it); else, on a panel's first appearance, the
        auto-grid slot and default size. A directive that fully pins position
        (``x`` and ``y``) doesn't consume a grid slot, so auto-placed cells don't
        leave a gap for it.
        """
        place = {}
        prev = self.canvas._named.get(name)
        pins_position = "x" in directive and "y" in directive
        if prev is not None and prev.x is not None:
            # Re-run: start from the panel's live geometry (user moves included).
            place.update(x=prev.x, y=prev.y, w=prev.w, h=prev.h,
                         rotation=prev.rotation, locked=prev.locked,
                         movable=prev.movable, resizable=prev.resizable,
                         interactive=prev.interactive)
        elif not pins_position:
            # First appearance, no explicit position: take the next grid slot.
            x, y = self._place(result)
            place.update(x=x, y=y, w=self.slot_w, h=self.slot_h)
        else:
            place.update(w=self.slot_w, h=self.slot_h)
        place.update(directive)  # explicit code directive overrides everything
        return place

    def _place(self, result):
        """Return the (x, y) for this cell, reusing its slot across re-runs."""
        key = self._cell_key(result)
        slot = self._slots.get(key)
        if slot is None:
            slot = self._next_slot
            self._slots[key] = slot
            self._next_slot += 1
        ox, oy = self.origin
        col = slot % self.cols
        row = slot // self.cols
        x = ox + col * (self.slot_w + self.gap)
        y = oy + row * (self.slot_h + self.gap)
        return x, y

    def _panel_name(self, result):
        """The canvas name (identity / eviction key) for this cell's panel."""
        return f"cell_{self._cell_key(result)}"

    def _cell_key(self, result):
        """A stable identity for the executed cell (for panel naming/slots)."""
        info = result.info
        cell_id = getattr(info, "cell_id", None)
        if cell_id:
            return str(cell_id)
        # Older IPython without cell_id: fall back to the raw source, so an
        # unchanged cell at least keeps swapping instead of stacking.
        return f"src:{hash(info.raw_cell)}"

    # -- rendering -----------------------------------------------------------
    def _build(self, out, name, result, label=None):
        """Pick and construct the panel component for a cell output value.

        ``label`` overrides the default source-line caption (from a
        ``# pycanvas: label=...`` directive); ``None`` keeps the default.
        """
        caption = label if label is not None else self._caption(result)

        # Plotly figures: route through the existing Plot wrapper (interactive).
        if _is_plotly(out):
            plot = Plot(name=name, label=caption,
                        width=self.slot_w, height=self.slot_h)
            plot.update(out)
            return plot

        # Everything else: ask Jupyter's formatter for the richest MIME rep and
        # drop it into a Custom (sandboxed iframe), matching inline rendering.
        html = self._render_html(out)
        if html is not None:
            return Custom(html=html, name=name, label=caption,
                          width=self.slot_w, height=self.slot_h)

        # No rich rep available: a plain text Label.
        return Label(name=name, value=_short_repr(out), label=caption)

    def _render_html(self, out):
        """Return an HTML body for ``out`` via the IPython display formatter.

        Falls back across HTML -> raster image -> SVG. Returns ``None`` when the
        formatter offers nothing richer than plain text (handled by the caller).
        """
        try:
            data, _ = self._ip.display_formatter.format(out)
        except Exception:
            data = {}
        if "text/html" in data:
            return _document(data["text/html"])
        for mime in ("image/png", "image/jpeg"):
            if mime in data:
                b64 = data[mime].strip()
                return _document(
                    f"<img style='max-width:100%;height:auto' "
                    f"src='data:{mime};base64,{b64}'>"
                )
        if "image/svg+xml" in data:
            return _document(data["image/svg+xml"])
        return None

    def _caption(self, result):
        """A short panel caption derived from the cell's source (or its id)."""
        if not self.include_source:
            return None
        src = result.info.raw_cell or ""
        # Caption from the first real line of code, skipping blanks and the
        # ``# pycanvas:`` directive line (it's configuration, not the output).
        for line in src.splitlines():
            stripped = line.strip()
            if not stripped or _DIRECTIVE_RE.match(line):
                continue
            return stripped[:60] + ("…" if len(stripped) > 60 else "")
        return None


# -- module helpers ----------------------------------------------------------
_DOC_STYLE = (
    "<style>body{margin:0;padding:8px;font-family:system-ui,sans-serif;"
    "font-size:13px;color:#111;background:#fff;box-sizing:border-box}"
    "table{border-collapse:collapse}img{display:block}</style>"
)


def _document(body):
    """Wrap an HTML fragment in a minimal styled document for the iframe."""
    return f"<!doctype html><html><head>{_DOC_STYLE}</head><body>{body}</body></html>"


def _is_plotly(obj):
    """Duck-type a Plotly figure without importing plotly."""
    mod = type(obj).__module__ or ""
    return mod.startswith("plotly") and callable(getattr(obj, "to_html", None))


def _short_repr(obj, limit=2000):
    """A length-bounded HTML-safe repr for the plain-text fallback."""
    text = repr(obj)
    if len(text) > limit:
        text = text[:limit] + " …"
    return text


# A ``# pycanvas: ...`` line anywhere in the cell carries per-cell overrides.
_DIRECTIVE_RE = re.compile(r"^[ \t]*#\s*pycanvas:[ \t]*(.*?)[ \t]*$",
                           re.IGNORECASE | re.MULTILINE)
# Placement/lock keys forwarded straight to ``Canvas.insert`` (numbers/bools),
# plus ``name``/``label`` (strings) which the caller pulls off first.
_NUMERIC_KEYS = {"x", "y", "w", "h", "rotation"}
_BOOL_KEYS = {"locked", "movable", "resizable", "interactive"}
_STR_KEYS = {"name", "label"}
_DIRECTIVE_KEYS = _NUMERIC_KEYS | _BOOL_KEYS | _STR_KEYS


def _coerce(value):
    """Parse a directive value into a number, bool, or stripped string."""
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value.strip("'\"")


def _parse_directive(raw_cell):
    """Extract per-cell overrides from a ``# pycanvas:`` line in the source.

    Returns a dict of recognised options (``{}`` when there's no directive). A
    bare ``skip`` token maps to ``{"skip": True}`` so the cell is left off the
    canvas. Recognised keys: ``x y w h rotation`` (numbers), ``locked movable
    resizable interactive`` (true/false), and ``name``/``label`` (strings).
    Pairs are space- or comma-separated, e.g.::

        # pycanvas: x=40 y=80 w=600 h=400 movable=false
        # pycanvas: skip
        # pycanvas: name=metrics, label="Live metrics", locked=true
    """
    if not raw_cell:
        return {}
    m = _DIRECTIVE_RE.search(raw_cell)
    if not m:
        return {}
    out = {}
    for token in re.split(r"[,\s]+", m.group(1).strip()):
        if not token:
            continue
        if token.lower() == "skip":
            out["skip"] = True
            continue
        key, sep, value = token.partition("=")
        key = key.strip().lower()
        if not sep:
            warnings.warn(f"pycanvas directive: ignoring bare token {token!r} "
                          f"(expected key=value)", stacklevel=3)
            continue
        if key not in _DIRECTIVE_KEYS:
            warnings.warn(f"pycanvas directive: unknown option {key!r}",
                          stacklevel=3)
            continue
        out[key] = _coerce(value)
    return out


def autopanel(canvas, cols=3, slot_w=520, slot_h=420, gap=40, origin=(0, 0),
              include_source=True):
    """Mirror every subsequent notebook cell's output onto ``canvas``.

    Registers an IPython ``post_run_cell`` hook so each cell that ends in an
    expression gets its own panel, auto-arranged in a grid. Re-running a cell
    refreshes its panel in place. Returns the :class:`CellCapture` controller;
    call :meth:`CellCapture.stop` (or :meth:`Canvas.stop_capturing_cells`) to
    stop.

    ``cols`` is the grid width; ``slot_w``/``slot_h`` the panel size in pixels;
    ``gap`` the spacing between panels; ``origin`` the top-left canvas
    coordinate of the grid. ``include_source=False`` drops the source-line
    caption from each panel.

    Idempotent: calling it again on the same canvas returns the existing
    capture rather than registering a second hook.
    """
    existing = getattr(canvas, "_cell_capture", None)
    if existing is not None:
        return existing
    capture = CellCapture(canvas, cols=cols, slot_w=slot_w, slot_h=slot_h,
                          gap=gap, origin=origin, include_source=include_source)
    capture.start()
    canvas._cell_capture = capture
    return capture
