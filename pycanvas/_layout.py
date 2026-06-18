"""Auto-layout containers — ``canvas.grid`` / ``column`` / ``row`` and the flow
placer behind them.

Split out of :mod:`pycanvas.canvas`: this is pure placement logic with no
dependency on the component classes or the wire, so it lives on its own and is
mixed into :class:`~pycanvas.canvas.Canvas` via :class:`_LayoutMixin`.
"""


class _FlowLayout:
    """Auto-placer for panels inserted inside its ``with`` block.

    Created by :meth:`Canvas.grid` / :meth:`Canvas.column` / :meth:`Canvas.row`.
    While active (pushed on ``canvas._layout_stack``) it hands a position to any
    insert that didn't get an explicit one — see ``Canvas.insert``.

    A ``grid`` lays uniform ``slot`` cells out ``cols`` per row. A ``column`` /
    ``row`` flows along one axis and lets each panel keep its *natural* size on
    the other (a slider stays slider-tall, a button button-tall), advancing the
    cursor by the size the panel actually occupies — so a mixed control strip
    isn't squashed to one height.
    """

    def __init__(self, canvas, kind, *, cols=1, slot=(None, None), gap=16,
                 origin=(40, 40), roles=None, client_id=None):
        self._canvas = canvas
        self._kind = kind                 # "grid" | "column" | "row"
        self._cols = cols
        self._slot_w, self._slot_h = slot  # either may be None (= natural)
        self._gap = gap
        self._ox, self._oy = origin
        self._i = 0
        self._cursor = list(origin)        # running (x, y) for column/row flow
        self._members = []                 # panels placed here, in insert order
                                           # (for refit, which re-flows them)
        # When set, the layout this container computes is written as a per-viewer
        # *overlay* (via set_layout) for these roles / this client, not the shared
        # base — so one role can have its own arrangement (precedence shared <
        # role < client). None = lay out the shared base, as usual.
        self._roles = roles
        self._client_id = client_id

    def __enter__(self):
        self._canvas._layout_stack.append(self)
        return self

    def __exit__(self, *exc):
        # Only unwind ourselves; nested `with` blocks pop in LIFO order anyway.
        if self._canvas._layout_stack and self._canvas._layout_stack[-1] is self:
            self._canvas._layout_stack.pop()
        return False

    def _place(self, component, w, h, auto_h):
        """Return ``(x, y, w, h)`` for the next panel inside this container.

        The slot fills in only dimensions the caller left blank (a ``None`` slot
        dimension keeps the component's own size); an ``h="auto"`` panel keeps
        fitting its content rather than being pinned to the slot height.
        """
        self._members.append(component)
        if w is None and self._slot_w is not None:
            w = self._slot_w
        if h is None and self._slot_h is not None and not auto_h:
            h = self._slot_h
        # The footprint this panel occupies, for advancing a column/row cursor:
        # the size it'll actually get (explicit or slot), else its own default.
        occ_w = w if w is not None else component.w
        occ_h = h if h is not None else component.h
        if self._kind == "grid":
            col, row = self._i % self._cols, self._i // self._cols
            x = self._ox + col * (self._slot_w + self._gap)
            y = self._oy + row * (self._slot_h + self._gap)
        elif self._kind == "column":
            x, y = self._ox, self._cursor[1]
            self._cursor[1] += occ_h + self._gap
        else:  # "row"
            x, y = self._cursor[0], self._oy
            self._cursor[0] += occ_w + self._gap
        self._i += 1
        return x, y, w, h

    def refit(self):
        """Re-pack this container's panels at their **current** sizes.

        A ``column``/``row`` places each panel once, when it's inserted, using
        the size known then — but an ``h="auto"`` panel only learns its real
        content height later (it's measured in the browser), and a panel resized
        afterwards (auto-height content that grew, a plot you enlarged) can then
        extend over or under its neighbour. ``refit`` re-flows the panels this
        block placed — same order, same ``origin``/``gap`` — at their *present*
        size, so the stacking and gaps are restored::

            with canvas.column(origin=(1060, 40)) as col:
                canvas.table(HPARAMS, h="auto")
                preds = canvas.image(grid_for(0), h="auto")
                canvas.markdown(run_log(), h="auto")

            preds.update(grid_for(step))   # taller now — pushes into the panel below
            col.refit()                    # restore the gaps

        It is **manual** by design: a panel that routinely changes height won't
        make its neighbours jitter — they only move when you call ``refit``. For
        a ``column``/``row`` the re-pack runs in the browser, where each panel's
        real measured size lives, and it accounts for a resize you triggered in
        the *same breath* (e.g. the ``log.update`` just above a ``refit``): it
        packs immediately and then once more when that panel's new height settles,
        so you don't have to defer the call. Panels removed since insert are
        skipped. Returns ``self``.
        """
        # A shared (un-scoped) column/row hands the re-pack to the browser: it
        # knows each panel's true measured height *now* and re-runs once the
        # pending content-fit lands, so a just-grown auto-height panel is packed
        # at its new size, not the stale one Python last heard about. A grid
        # (uniform fixed slots) or a per-viewer-scoped container can't express
        # that as a shared reflow, so they re-pack from Python-side sizes instead.
        if self._kind in ("column", "row") \
                and self._roles is None and self._client_id is None:
            self._refit_remote()
        else:
            self._refit_local()
        return self

    def _refit_remote(self):
        live = set(self._canvas._components)
        ids = [c.id for c in self._members if c in live]
        if ids:
            self._canvas._bridge.broadcast({
                "type": "reflow",
                "key": id(self),          # stable per container; repeats re-arm
                "ids": ids,               # insertion order = pack order
                "kind": self._kind,
                "x0": self._ox, "y0": self._oy, "gap": self._gap,
            })

    def _refit_local(self):
        live = set(self._canvas._components)
        cursor = [self._ox, self._oy]
        i = 0
        for comp in self._members:
            if comp not in live:
                continue  # removed/replaced since insert
            if self._kind == "grid":
                col, row = i % self._cols, i // self._cols
                x = self._ox + col * (self._slot_w + self._gap)
                y = self._oy + row * (self._slot_h + self._gap)
            elif self._kind == "column":
                x, y = self._ox, cursor[1]
                cursor[1] += comp.h + self._gap
            else:  # "row"
                x, y = cursor[0], self._oy
                cursor[0] += comp.w + self._gap
            i += 1
            # Reposition only — width/height stay as the panel currently is (a
            # column keeps natural heights, a manual resize is respected); the
            # overlap is purely a placement problem. Scoped containers re-emit the
            # move as the same audience's overlay, matching the original placement.
            if self._roles is None and self._client_id is None:
                comp.set_layout(x=x, y=y)
            else:
                comp.set_layout(x=x, y=y, roles=self._roles,
                                client_id=self._client_id)


class _LayoutMixin:
    """``Canvas`` methods that open an auto-layout ``with`` block. Mixed into
    Canvas; each returns a :class:`_FlowLayout` bound to ``self``."""

    def grid(self, cols=2, slot=(520, 360), gap=24, origin=(40, 40),
             roles=None, client_id=None):
        """Auto-arrange panels added inside a ``with`` block into a grid.

        Inside the block, any panel inserted without an explicit ``x``/``y`` (or a
        ``below=``/``right_of=`` anchor) drops into the next cell — left to right,
        top to bottom, ``cols`` per row — taking the slot size unless you pass
        ``w``/``h``::

            with canvas.grid(cols=2, slot=(560, 300)):
                canvas.live_plot("loss")
                canvas.live_plot("accuracy")
                canvas.image(fig)            # next row

        ``slot`` is each cell's ``(width, height)``, ``gap`` the spacing between
        cells, ``origin`` the grid's top-left canvas coordinate. An explicit
        position or relative anchor still wins for that panel. Nest or sequence
        blocks freely to build columns of charts beside columns of media. For a
        strip of mixed-height controls, prefer :meth:`column` / :meth:`row`,
        which keep each panel's natural size instead of a uniform cell.

        Pass ``roles=`` and/or ``client_id=`` to lay the block out for just those
        viewers — each panel's slot is written as that audience's *overlay* (via
        :meth:`~pycanvas.React.set_layout`) instead of the shared base, so one
        role can have its own arrangement. Best for a role's *exclusive* panels;
        a panel shared across roles is created once (in one block), so give the
        other roles their layout with a separate scoped block over fresh panels
        or `set_layout(roles=…)` directly.
        """
        return _FlowLayout(self, "grid", cols=cols, slot=slot, gap=gap,
                           origin=origin, roles=roles, client_id=client_id)

    def column(self, width=None, gap=16, origin=(40, 40), w=None,
               roles=None, client_id=None):
        """Auto-stack panels added inside a ``with`` block into one column.

        Each panel keeps its **natural height** (a slider stays slider-tall, a
        button button-tall), so a mixed control strip isn't squashed to one
        height. ``width`` sets a common width (``None`` keeps each panel's own);
        ``gap`` is the vertical spacing, ``origin`` the top-left corner. An
        explicit position or relative anchor still wins for that panel. ``w`` is
        accepted as an alias for ``width``. ``roles=`` / ``client_id=`` scope the
        arrangement to those viewers (see :meth:`grid`).

        Placement is one-shot, at insert; if a panel later grows (auto-height
        content, a resized plot) it can overlap its neighbour. Keep the returned
        container and call :meth:`~_FlowLayout.refit` to re-pack the column at the
        panels' current sizes.
        """
        if w is not None:
            if width is not None:
                raise TypeError("pass either width= or w=, not both")
            width = w
        return _FlowLayout(self, "column", slot=(width, None), gap=gap,
                           origin=origin, roles=roles, client_id=client_id)

    def row(self, height=None, gap=16, origin=(40, 40), h=None,
            roles=None, client_id=None):
        """Auto-arrange panels added inside a ``with`` block into one row.

        The horizontal counterpart of :meth:`column`: panels flow left to right,
        each keeping its **natural width**. ``height`` sets a common height
        (``None`` keeps each panel's own); ``gap`` is the horizontal spacing.
        ``h`` is accepted as an alias for ``height``. ``roles=`` / ``client_id=``
        scope the arrangement to those viewers (see :meth:`grid`).

        Placement is one-shot, at insert; if a panel later grows it can overlap
        its neighbour. Keep the returned container and call
        :meth:`~_FlowLayout.refit` to re-pack the row at the panels' current
        sizes.
        """
        if h is not None:
            if height is not None:
                raise TypeError("pass either height= or h=, not both")
            height = h
        return _FlowLayout(self, "row", slot=(None, height), gap=gap,
                           origin=origin, roles=roles, client_id=client_id)
