"""Auto-layout containers â€” ``canvas.grid`` / ``column`` / ``row`` and the flow
placer behind them.

Split out of :mod:`danvas.canvas`: this is pure placement logic with no
dependency on the component classes or the wire, so it lives on its own and is
mixed into :class:`~danvas.canvas.Canvas` via :class:`_LayoutMixin`.
"""


class Container:
    """A persistent, nestable layout container.

    Created by :meth:`Canvas.column` / :meth:`Canvas.row`.  Children â€” panels
    or other containers â€” are added in order via :meth:`add`.  The container
    computes each child's position from the running cursor and broadcasts a
    ``container_sync`` message to the frontend, which auto-repacks the whole
    tree whenever any member's size changes (e.g. an ``h="auto"`` panel
    settling its content height).

    A *root* container has an explicit ``(x, y)`` anchor on the canvas.
    A *child* container is created without ``x``/``y``; the parent assigns its
    position during the layout pass.  Nest freely::

        sidebar = canvas.column(x=40, y=40, w=280, gap=16)
        sidebar.add(canvas.label("Controls"))
        sidebar.add(canvas.slider("lr", 0.001, 0.1))

        buttons = canvas.container("row", gap=8)
        buttons.add(canvas.button("Train"))
        buttons.add(canvas.button("Stop"))
        sidebar.add(buttons)          # row is child of column

    Or with context managers (auto-intercepts panels created in the block)::

        with canvas.column(x=40, y=40, w=280, gap=16) as sidebar:
            canvas.label("Controls")
            canvas.slider("lr", 0.001, 0.1)
            with sidebar.row(gap=8):
                canvas.button("Train")
                canvas.button("Stop")
    """

    # Duck-type compatibility with _FlowLayout so canvas.insert()'s existing
    # scoped-layout check (`flow._roles is None`) works without an isinstance guard.
    _roles = None
    _client_id = None

    def __init__(self, canvas, mode, x=None, y=None, w=None, h=None, gap=16,
                 fill_w=False, padding=0):
        self._canvas = canvas
        self._mode = mode        # "column" | "row"
        self._x = x
        self._y = y
        self._w = w              # explicit cross-axis width (column) or fixed row width
        self._h = h              # explicit cross-axis height (row) or fixed column height
        self._gap = gap
        self._fill_w = fill_w    # expand to viewport width in the browser (streamlit mode)
        self._padding = padding  # inset from each viewport edge when fill_w=True
        self._children = []      # ordered: BaseComponent | Container
        self._parent = None      # Container | None (root when None)
        self._key = str(id(self))

    # ---- context manager (for `with canvas.column() as col:`) -----------------

    def __enter__(self):
        self._canvas._layout_stack.append(self)
        return self

    def __exit__(self, *exc):
        if self._canvas._layout_stack and self._canvas._layout_stack[-1] is self:
            self._canvas._layout_stack.pop()
        return False

    # ---- child container factories --------------------------------------------

    def row(self, gap=16, h=None):
        """Create a child row container, add it to ``self``, return it.

        Can also be used as a context manager::

            with parent_col.row(gap=8) as row:
                canvas.button("A")
                canvas.button("B")
        """
        child = Container(self._canvas, "row", gap=gap, h=h)
        return self.add(child)

    def column(self, gap=16, w=None):
        """Create a child column container, add it to ``self``, return it."""
        child = Container(self._canvas, "column", gap=gap, w=w)
        return self.add(child)

    # ---- public API -----------------------------------------------------------

    def add(self, child):
        """Add a panel or nested container in order. Returns the child.

        Immediately repacks the whole tree at current sizes and broadcasts
        the updated layout to connected browsers.  The browser auto-repacks
        again whenever any member's size settles (e.g. ``h="auto"`` content
        fitting), so you do not need to call :meth:`reflow` in the common
        case.
        """
        if isinstance(child, Container):
            if child._parent is not None:
                raise ValueError(
                    "this container already belongs to another container; "
                    "remove it first before re-parenting")
            child._parent = self
        self._children.append(child)
        # Update bridge's panel-to-container index so auto-repack can find
        # the root when a panel's size changes.
        bridge = getattr(self._canvas, "_bridge", None)
        if bridge is not None:
            if isinstance(child, Container):
                child._index_panels(bridge)
            else:
                bridge._panel_in_container[child.id] = self
        # Repack from the root and broadcast the updated container tree.
        root = self._root()
        if root._x is not None and root._y is not None:
            root._sync()
        return child

    def _insert_at(self, index, child):
        if isinstance(child, Container):
            if child._parent is not None:
                raise ValueError(
                    "this container already belongs to another container; "
                    "remove it first before re-parenting")
            child._parent = self
        self._children.insert(index, child)
        bridge = getattr(self._canvas, "_bridge", None)
        if bridge is not None:
            if isinstance(child, Container):
                child._index_panels(bridge)
            else:
                bridge._panel_in_container[child.id] = self
        root = self._root()
        if root._x is not None and root._y is not None:
            root._sync()
        return child

    def insert_before(self, ref, child):
        """Insert ``child`` immediately before ``ref`` in this container. Returns ``child``.

        ``ref`` must already be a direct member of this container.
        Repacks and broadcasts immediately.
        """
        return self._insert_at(self._children.index(ref), child)

    def insert_after(self, ref, child):
        """Insert ``child`` immediately after ``ref`` in this container. Returns ``child``.

        ``ref`` must already be a direct member of this container.
        Repacks and broadcasts immediately.
        """
        return self._insert_at(self._children.index(ref) + 1, child)

    def remove(self, child):
        """Remove a child panel or container from this container. Returns ``self``."""
        try:
            self._children.remove(child)
        except ValueError:
            return self
        bridge = getattr(self._canvas, "_bridge", None)
        if isinstance(child, Container):
            child._parent = None
            if bridge is not None:
                child._deindex_panels(bridge)
        else:
            if bridge is not None:
                bridge._panel_in_container.pop(child.id, None)
        root = self._root()
        if root._x is not None and root._y is not None:
            root._sync()
        return self

    def reflow(self):
        """Re-pack the full container tree at current sizes. Returns ``self``.

        The browser auto-repacks on every size change, so this is only needed
        when you want to force an immediate Python-side repack (e.g. after
        changing a container's ``gap`` or repositioning a child externally).
        """
        root = self._root()
        if root._x is not None and root._y is not None:
            root._sync()
        return self

    def refit(self):
        """Alias for :meth:`reflow` â€” kept for backwards compatibility."""
        return self.reflow()

    def move(self, x, y):
        """Move this container's root anchor and repack children. Returns ``self``."""
        root = self._root()
        root._x = x
        root._y = y
        if root._x is not None and root._y is not None:
            root._sync()
        return self

    # ---- bounding box (used by a parent container to advance its cursor) ------

    @property
    def w(self):
        """Current bounding width.  Returns ``_w`` if fixed, else derived."""
        if self._w is not None:
            return self._w
        if not self._children:
            return 0
        if self._mode == "row":
            ws = [c.w for c in self._children if c.w]
            return sum(ws) + self._gap * max(0, len(ws) - 1)
        # column: cross-axis = widest child
        return max((c.w for c in self._children if c.w), default=0)

    @property
    def h(self):
        """Current bounding height.  Returns ``_h`` if fixed, else derived."""
        if self._h is not None:
            return self._h
        if not self._children:
            return 0
        if self._mode == "column":
            hs = [c.h for c in self._children if c.h]
            return sum(hs) + self._gap * max(0, len(hs) - 1)
        # row: cross-axis = tallest child
        return max((c.h for c in self._children if c.h), default=0)

    # ---- called by canvas.insert() on the context-manager path ---------------

    def _place(self, component, w, h, auto_h):
        """Return ``(x, y, w, h)`` for a new panel entering via the layout stack.

        Called by :meth:`~danvas.canvas.Canvas.insert` when this container is
        active.  Appends the component to ``_children`` and computes its position
        by summing the sizes of already-placed siblings.  Does *not* call
        ``set_layout`` or broadcast â€” ``insert`` handles that after
        ``register_live``.
        """
        # Apply cross-axis sizing from the container if not caller-specified.
        if w is None and self._w is not None:
            w = self._w
        if h is None and self._h is not None and not auto_h:
            h = self._h

        # Compute position by summing current children's footprints.
        if self._x is None or self._y is None:
            x = y = None
        else:
            cx, cy = self._x, self._y
            for child in self._children:
                if self._mode == "column":
                    cy += (child.h or 0) + self._gap
                else:
                    cx += (child.w or 0) + self._gap
            x = cx if self._mode == "row" else self._x
            y = cy if self._mode == "column" else self._y

        # Track the component as a member and register it in the bridge index.
        self._children.append(component)
        bridge = getattr(self._canvas, "_bridge", None)
        if bridge is not None:
            bridge._panel_in_container[component.id] = self

        return x, y, w, h

    def _post_place(self):
        """Broadcast the updated container state after canvas.insert() registers the panel."""
        self._root()._broadcast()

    # ---- internal layout / broadcast -----------------------------------------

    def _root(self):
        """Walk parent links to find the root container."""
        node = self
        while node._parent is not None:
            node = node._parent
        return node

    def _index_panels(self, bridge):
        """Register all panels in this subtree in ``bridge._panel_in_container``."""
        for child in self._children:
            if isinstance(child, Container):
                child._index_panels(bridge)
            else:
                bridge._panel_in_container[child.id] = self

    def _deindex_panels(self, bridge):
        """Remove all panels in this subtree from ``bridge._panel_in_container``."""
        for child in self._children:
            if isinstance(child, Container):
                child._deindex_panels(bridge)
            else:
                bridge._panel_in_container.pop(child.id, None)

    def _sync(self):
        """Python-side repack: compute positions for all children then broadcast."""
        self._layout(self._x, self._y)
        self._broadcast()

    def _layout(self, ox, oy):
        """Recursively compute and apply positions for all children.

        ``ox``/``oy`` are this container's top-left origin (may differ from
        ``_x``/``_y`` when called recursively by a parent container).
        """
        cx, cy = ox, oy
        for child in self._children:
            child_x = cx if self._mode == "row" else ox
            child_y = cy if self._mode == "column" else oy
            if isinstance(child, Container):
                # Update the child's stored position so _collect_msgs is accurate.
                child._x = child_x
                child._y = child_y
                child._layout(child_x, child_y)
            else:
                kw = {"x": child_x, "y": child_y}
                if self._mode == "column" and self._w is not None:
                    kw["w"] = self._w
                elif self._mode == "row" and self._h is not None:
                    kw["h"] = self._h
                child.set_layout(**kw)
            if self._mode == "column":
                cy += (child.h or 0) + self._gap
            else:
                cx += (child.w or 0) + self._gap

    def _broadcast(self):
        """Send ``container_sync`` messages for this whole subtree to all clients."""
        bridge = getattr(self._canvas, "_bridge", None)
        if bridge is None:
            return
        msgs = []
        self._collect_msgs(msgs)
        for msg in msgs:
            bridge.broadcast(msg)
            bridge.store_container(msg)

    def _collect_msgs(self, msgs):
        """Build ``container_sync`` wire messages depth-first (children before parent)."""
        members = []
        for child in self._children:
            if isinstance(child, Container):
                child._collect_msgs(msgs)
                members.append({"kind": "container", "key": child._key})
            else:
                members.append({"kind": "panel", "id": child.id})
        msgs.append({
            "type": "container_sync",
            "key": self._key,
            "mode": self._mode,
            "x0": self._x,
            "y0": self._y,
            "gap": self._gap,
            "w": self._w,
            "h": self._h,
            "fill_w": self._fill_w or None,   # omit when False to keep messages lean
            "padding": self._padding or None,
            "members": members,
        })


class _FlowLayout:
    """Auto-placer for ``canvas.grid`` â€” uniform fixed-slot grid layout.

    :meth:`Canvas.column` and :meth:`Canvas.row` now return a
    :class:`Container` instead.  This class is kept for ``grid``, which has
    different semantics (uniform cells, no cascading height).
    """

    # Duck-type compatibility: canvas.insert() checks these.
    _roles = None
    _client_id = None

    def __init__(self, canvas, kind, *, cols=1, slot=(None, None), gap=16,
                 origin=(40, 40), roles=None, client_id=None):
        self._canvas = canvas
        self._kind = kind                 # "grid" only (column/row moved to Container)
        self._cols = cols
        self._slot_w, self._slot_h = slot
        self._gap = gap
        self._ox, self._oy = origin
        self._i = 0
        self._cursor = list(origin)
        self._members = []
        self._roles = roles
        self._client_id = client_id

    def __enter__(self):
        self._canvas._layout_stack.append(self)
        return self

    def __exit__(self, *exc):
        if self._canvas._layout_stack and self._canvas._layout_stack[-1] is self:
            self._canvas._layout_stack.pop()
        return False

    def _place(self, component, w, h, auto_h):
        """Return ``(x, y, w, h)`` for the next panel inside this grid."""
        self._members.append(component)
        if w is None and self._slot_w is not None:
            w = self._slot_w
        if h is None and self._slot_h is not None and not auto_h:
            h = self._slot_h
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
        """Re-pack this grid's panels at their current sizes."""
        self._refit_local()
        return self

    def _refit_local(self):
        live = set(self._canvas._components)
        cursor = [self._ox, self._oy]
        i = 0
        for comp in self._members:
            if comp not in live:
                continue
            if self._kind == "grid":
                col, row = i % self._cols, i // self._cols
                x = self._ox + col * (self._slot_w + self._gap)
                y = self._oy + row * (self._slot_h + self._gap)
            elif self._kind == "column":
                x, y = self._ox, cursor[1]
                cursor[1] += comp.h + self._gap
            else:
                x, y = cursor[0], self._oy
                cursor[0] += comp.w + self._gap
            i += 1
            if self._roles is None and self._client_id is None:
                comp.set_layout(x=x, y=y)
            else:
                comp.set_layout(x=x, y=y, roles=self._roles,
                                client_id=self._client_id)


class _LayoutMixin:
    """``Canvas`` methods that open an auto-layout block. Mixed into Canvas."""

    def container(self, mode, x=None, y=None, w=None, h=None, gap=16,
                  origin=None):
        """Create a layout container with the given ``mode`` (``"column"`` or ``"row"``).

        ``x``/``y`` anchor the container on the canvas (required for root
        containers; omit for containers that will be nested inside another).
        ``w`` fixes each child's width in a column (or the container's total
        width in a row); ``h`` fixes each child's height in a row (or the
        container's total height in a column).  ``gap`` is the spacing between
        children.  ``origin`` is accepted as an alias for ``(x, y)``.

        Use :meth:`Container.add` to add panels or nested containers, or
        use the returned object as a context manager to auto-intercept panels
        created inside the block::

            # Explicit add
            col = canvas.container("column", x=40, y=40, w=300, gap=16)
            col.add(canvas.slider("lr"))
            row = col.container("row", gap=8)
            row.add(canvas.button("Train"))
            row.add(canvas.button("Stop"))

            # Context manager
            with canvas.container("column", x=40, y=40, w=300, gap=16) as col:
                canvas.slider("lr")
                with col.container("row", gap=8):
                    canvas.button("Train")
                    canvas.button("Stop")
        """
        if mode not in ("column", "row"):
            raise ValueError(f"container mode must be 'column' or 'row', got {mode!r}")
        if origin is not None:
            if x is None:
                x = origin[0]
            if y is None:
                y = origin[1]
        return Container(self, mode, x=x, y=y, w=w, h=h, gap=gap)

    def column(self, x=None, y=None, w=None, gap=16):
        """Stack panels top-to-bottom.

        Returns a :class:`Container` in ``"column"`` mode anchored at ``x``/``y``
        (default 40, 40).  ``w`` pins a common width for every child; omit it to
        let each child keep its natural width.  ``gap`` is the vertical spacing.

        Use :meth:`~Container.add` explicitly or open a ``with`` block to capture
        panels automatically.  Call :meth:`~Container.row` / :meth:`~Container.column`
        on the returned container to nest sub-containers inside it.
        """
        return Container(self, "column", x=x if x is not None else 40,
                         y=y if y is not None else 40, w=w, gap=gap)

    def row(self, x=None, y=None, h=None, gap=16):
        """Arrange panels left-to-right.

        Returns a :class:`Container` in ``"row"`` mode anchored at ``x``/``y``
        (default 40, 40).  ``h`` pins a common height for every child; omit it to
        let each child keep its natural height.  ``gap`` is the horizontal spacing.

        Use :meth:`~Container.add` explicitly or open a ``with`` block to capture
        panels automatically.  Call :meth:`~Container.column` to nest a column
        inside this row.
        """
        return Container(self, "row", x=x if x is not None else 40,
                         y=y if y is not None else 40, h=h, gap=gap)

    def streamlit(self, gap=16, padding=0):
        """Streamlit-style layout: vertical scrolling, panels stacked top-to-bottom
        spanning the full browser viewport width.

        Sets the camera to ``scroll_y`` mode (vertical scroll only, zoom locked
        at 1Ã—) and returns a root :class:`Container` in column mode whose width
        tracks the browser window dynamically â€” so every panel added to it is
        automatically as wide as the viewport regardless of screen size.

        The returned container works exactly like :meth:`column`: use
        :meth:`~Container.add`, :meth:`~Container.row`, or the context-manager
        form to build the layout::

            page = canvas.streamlit(gap=24, padding=20)
            page.add(canvas.label("title", "My App", h=48))
            page.add(canvas.markdown("â€¦bodyâ€¦", h="auto"))

            with canvas.streamlit(gap=16) as page:
                canvas.label("title", "My App", h=48)
                with page.row(gap=8):
                    canvas.button("train", text="Train", w=100)
                    canvas.button("stop",  text="Stop",  w=100)

        ``gap`` is the vertical spacing between panels; ``padding`` insets the
        content from both sides of the viewport (so effective width is
        ``viewport_width - 2 * padding``).
        """
        self.set_view(navigation="scroll_y")
        return Container(self, "column", x=padding, y=0,
                         fill_w=True, padding=padding, gap=gap)

    def grid(self, cols=2, slot=(520, 360), gap=24, origin=(40, 40),
             roles=None, client_id=None):
        """Auto-arrange panels added inside a ``with`` block into a grid.

        Inside the block, any panel inserted without an explicit ``x``/``y`` (or a
        ``below=``/``right_of=`` anchor) drops into the next cell â€” left to right,
        top to bottom, ``cols`` per row â€” taking the slot size unless you pass
        ``w``/``h``::

            with canvas.grid(cols=2, slot=(560, 300)):
                canvas.live_plot("loss")
                canvas.live_plot("accuracy")
                canvas.image(fig)            # next row

        ``slot`` is each cell's ``(width, height)``, ``gap`` the spacing between
        cells, ``origin`` the grid's top-left canvas coordinate.
        """
        return _FlowLayout(self, "grid", cols=cols, slot=slot, gap=gap,
                           origin=origin, roles=roles, client_id=client_id)
