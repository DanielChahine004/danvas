"""Component factory methods for :class:`~pycanvas.canvas.Canvas`
(``canvas.slider`` / ``button`` / ``react`` / …).

Split out of canvas.py: each is a thin wrapper that builds a component and hands
it to ``Canvas.insert`` via ``_make``. Mixed into Canvas as :class:`_FactoryMixin`,
so the methods run on the real Canvas instance — ``self.insert`` /
``self._auto_name`` / ``self._show_seq`` resolve there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._flags import LAYOUT_FLAGS
from .components import (
    AudioFeed,
    Button,
    Chat,
    Custom,
    Download,
    FileBrowser,
    Histogram,
    Image,
    Inspector,
    Label,
    LivePlot,
    Markdown,
    Plot,
    React,
    Repl,
    Slider,
    Table,
    TextField,
    Toggle,
    Upload,
    VideoFeed,
    WebView,
)

if TYPE_CHECKING:
    from typing_extensions import Unpack

    from .canvas import Place

# Keyword names consumed by ``Canvas.insert`` itself. A factory splits these off
# and forwards everything else to the component constructor. ``name`` is
# intentionally absent: it is the component's identity (set in its constructor),
# not a placement option. The lock/chrome flags come from the shared LAYOUT_FLAGS
# table; ``queue`` lives here so all factories accept it uniformly.
_INSERT_KEYS = ("x", "y", "w", "h", "width", "height", "rotation", "queue",
                "below", "above", "right_of", "left_of", "gap",
                "roles", "lock_for", *LAYOUT_FLAGS)


class _FactoryMixin:
    """The ``canvas.<component>(...)`` factories. Mixed into :class:`Canvas`."""

    def _make(self, cls, *args, **kw):
        place = {k: kw.pop(k) for k in _INSERT_KEYS if k in kw}
        return self.insert(cls(*args, **kw), **place)

    def slider(self, name, min=0, max=100, default=None, step=1,
               on_release=False, label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Slider`. See :meth:`insert` for ``place``.

        ``step`` sets the granularity and the int-vs-float behaviour (a
        fractional step like ``0.1`` makes it a float slider). ``on_release=True``
        reports only the settled value when the user lets go, instead of every
        value during the drag.
        """
        return self._make(Slider, name, min=min, max=max, default=default,
                          step=step, on_release=on_release, label=label, **place)

    def toggle(self, name, options, default=None, label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Toggle`. See :meth:`insert` for ``place``."""
        return self._make(Toggle, name, options, default=default, label=label,
                          **place)

    def button(self, name, text=None, label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Button`. See :meth:`insert` for ``place``."""
        return self._make(Button, name, text=text, label=label, **place)

    def label(self, name, value="", label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Label`. See :meth:`insert` for ``place``."""
        return self._make(Label, name, value=value, label=label, **place)

    def video(self, name, quality=70, label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.VideoFeed`. See :meth:`insert` for ``place``."""
        return self._make(VideoFeed, name, quality=quality, label=label, **place)

    def audio(self, name, sample_rate=16000, channels=1, label=None, **place: Unpack[Place]):
        """Insert an :class:`~pycanvas.AudioFeed`. See :meth:`insert` for ``place``."""
        return self._make(AudioFeed, name, sample_rate=sample_rate,
                          channels=channels, label=label, **place)

    def chat(self, name="chat", label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Chat` panel. See :meth:`insert` for ``place``."""
        return self._make(Chat, name=name, label=label, **place)

    def custom(self, html=None, path=None, css=None, js=None, name="custom",
               label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Custom`. See :meth:`insert` for ``place``.

        ``html``/``css``/``js`` may be given as separate strings (e.g. pasted
        from uiverse.io) — they are composed into one document under the hood.
        Size the panel with ``w``/``h`` in ``place``.
        """
        return self._make(Custom, html=html, path=path, css=css, js=js,
                          name=name, label=label, **place)

    def download(self, name, source=None, filename=None, text=None, label=None,
                 **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Download` button. See :meth:`insert` for ``place``.

        Clicking it downloads ``source`` — a file path or ``bytes`` — to the
        viewer's machine. For content generated fresh on each click, omit
        ``source`` and register a provider with ``@download.provide``.
        ``filename`` sets the saved name (otherwise a path's basename, or the
        panel name, is used). The host code chooses what each click serves, so
        nothing the viewer sends selects a path.
        """
        return self._make(Download, name, source=source, filename=filename,
                          text=text, label=label, **place)

    def upload(self, name="upload", text=None, label=None, dest=None,
               accept=None, multiple=False, max_size=None, **place: Unpack[Place]):
        """Insert an :class:`~pycanvas.Upload` panel. See :meth:`insert` for ``place``.

        A click-or-drop zone that receives a viewer's file into Python; wire it
        with ``@upload.on_upload``. By default the bytes arrive in memory
        (``file.data``); pass ``dest=`` a directory to stream each upload to disk
        instead (``file.path``), which keeps memory flat for large files.
        ``accept`` filters the picker (e.g. ``".csv"``), ``multiple=True`` allows
        several at once, and ``max_size`` (bytes) rejects oversized uploads — set
        it on any public/tunneled canvas.
        """
        return self._make(Upload, name, text=text, label=label, dest=dest,
                          accept=accept, multiple=multiple, max_size=max_size,
                          **place)

    def file_browser(self, name="files", root=".", label=None, pattern=None,
                     show_hidden=False, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.FileBrowser`. See :meth:`insert` for ``place``.

        Navigation is confined to ``root``. ``pattern`` (an fnmatch glob like
        ``"*.csv"``) filters which files are shown. Size it with ``w``/``h`` in
        ``place``.
        """
        return self._make(FileBrowser, name=name, root=root, label=label,
                          pattern=pattern, show_hidden=show_hidden, **place)

    def react(self, source=None, path=None, jsx=None, css=None, css_path=None,
              name="react", label=None, props=None, scope=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.React` panel — the workhorse for custom UI.

        ``source`` is JSX defining ``function Component(...)`` (or load it from a
        file with ``path=``); alternatively pass just ``jsx`` markup plus optional
        ``css`` and the Component wrapper is added under the hood. ``css`` also
        works with ``source=`` (it rides as a ``<style>`` the host renders), so a
        full component can keep its styles in a separate string — or load it from a
        file with ``css_path=`` (the ``css`` twin of ``path=``), so a panel keeps
        both halves in sibling files. Use :meth:`React.from_uiverse` to convert a
        uiverse.io styled-components snippet. ``props`` is the initial props dict; ``scope`` is third-party
        library names (e.g. ``["d3"]``) loaded as ESM and exposed as ``libs``.

        Placement, visibility (``roles`` / ``lock_for``), the lock/chrome flags,
        and ``queue`` all flow through ``**place`` — see :meth:`insert` (and the
        :class:`Place` keys your editor now autocompletes).
        """
        return self._make(React, source=source, path=path, jsx=jsx, css=css,
                          css_path=css_path, name=name, label=label, props=props,
                          scope=scope, **place)

    def markdown(self, text="", name="markdown", label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Markdown` panel. See :meth:`insert` for ``place``."""
        return self._make(Markdown, text=text, name=name, label=label, **place)

    def image(self, src, name="image", label=None, fit="contain", **place: Unpack[Place]):
        """Insert an :class:`~pycanvas.Image` panel. See :meth:`insert` for ``place``.

        ``src`` is a path, URL, image bytes, Matplotlib/PIL figure, or array.
        """
        return self._make(Image, src, name=name, label=label, fit=fit, **place)

    def table(self, data, name="table", label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Table` panel. See :meth:`insert` for ``place``.

        ``data`` is a pandas DataFrame/Series, a list of dicts/rows, or a dict.
        """
        return self._make(Table, data, name=name, label=label, **place)

    def text_field(self, name, placeholder="", default="", multiline=False,
                   label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.TextField`. See :meth:`insert` for ``place``.

        Single-line (default): fires ``on_change`` on Enter or focus-loss.
        Pass ``multiline=True`` for a textarea that fires on focus-loss.
        """
        return self._make(TextField, name, placeholder=placeholder,
                          default=default, multiline=multiline, label=label,
                          **place)

    def show(self, value, name=None, label=None, **place: Unpack[Place]):
        """Auto-render any value as the best-fitting panel and insert it.

        Picks the component the way a notebook decides how to render an output
        (DataFrame → table, figure/array → image, Plotly → plot, rich
        ``_repr_*`` → its HTML, dict/list → JSON, string → label/markdown, else a
        repr label) via :func:`pycanvas.panel_for`. With no ``name`` a unique one
        is generated; re-using a ``name`` replaces that panel in place. Returns
        the inserted component. See :meth:`insert` for ``place``.
        """
        from .dispatch import panel_for
        if name is None:
            self._show_seq += 1
            name = f"panel_{self._show_seq}"
        comp = panel_for(value, name=name, label=label)
        # insert() handles eviction of whatever currently holds this name, so
        # re-showing under the same name replaces in place on its own.
        return self.insert(comp, **place)

    def webview(self, url, name="web", label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.WebView`. See :meth:`insert` for ``place``."""
        return self._make(WebView, url, name=name, label=label, **place)

    def plot(self, name="plot", label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Plot`. See :meth:`insert` for ``place``."""
        return self._make(Plot, name=name, label=label, **place)

    def live_plot(self, name="live plot", **kw):
        """Insert a :class:`~pycanvas.LivePlot`.

        Constructor kwargs (``traces``, ``max_points``, ``mode``, ``layout``,
        ``smoothing``, ``w``, ``h``, ``label``) and :meth:`insert` placement
        options both go in ``kw``; they don't overlap. ``traces`` only fixes the
        legend order — pushing an unseen key adds a trace on the fly.
        """
        return self._make(LivePlot, name=name, **kw)

    def histogram(self, name="histogram", **kw):
        """Insert a :class:`~pycanvas.Histogram` — a distribution-over-time panel.

        Constructor kwargs (``bins``, ``mode``, ``value_range``, ``max_steps``,
        ``label``, ``w``, ``h``) and :meth:`insert` placement options both go in
        ``kw``. Feed it with ``panel.add(values, step)``; needs ``plotly``.
        """
        return self._make(Histogram, name=name, **kw)

    def repl(self, name="repl", label=None, **place: Unpack[Place]):
        """Insert a :class:`~pycanvas.Repl`. See :meth:`insert` for ``place``.

        Call :meth:`enable_repl` first to bind the namespace cells run against.
        """
        return self._make(Repl, name=name, label=label, **place)

    def inspector(self, name="inspector", refresh=None, source="components",
                  namespace=None, label=None, **place: Unpack[Place]):
        """Insert an :class:`~pycanvas.Inspector`. See :meth:`insert` for ``place``."""
        return self._make(Inspector, name=name, refresh=refresh, source=source,
                          namespace=namespace, label=label, **place)
