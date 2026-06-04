"""Repl: an ephemeral code cell that execs against a shared namespace.

For quick inspection and control from the canvas (e.g. ``print(canvas.servo.x)``)
-- not a source-of-truth notebook. The cell runs on the canvas :class:`Kernel`
thread so a slow statement can't freeze the UI, and its output (stdout, stderr,
and the repr of a trailing expression) is shown below the editor.

The namespace is supplied by :meth:`Canvas.enable_repl`; call it before
inserting a ``Repl``. Cell text is not persisted across serves by design.
"""

from .base import BaseComponent


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
