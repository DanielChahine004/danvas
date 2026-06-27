"""Live demo of canvas.on_dispatch + trace_calls — watch handlers AND the calls
they make, nested, in your terminal.

    python examples/dispatch_trace_demo.py

A browser tab opens. Click the buttons / drag the slider there, and watch THIS
terminal: every handler prints as it is queued (dim), starts (yellow >>), and
finishes (green OK) or errors (red XX), with timing. Because trace_calls() is on,
the trace also follows each handler INTO this file's own functions, indented by
call depth — so you see which function called which. Calls into danvas, the
stdlib, and packages are skipped, so the tree stays your code.

All the handlers (and nested calls) a single action fans out to share one
[trace NN] id. The two "Run parallel" handlers are threaded=True, so their trees
interleave. Ctrl+C to stop.
"""

import time

import danvas

canvas = danvas.Canvas()

# -- the trace tap: print each event, indented by call depth ------------------
# ASCII markers only — Windows consoles often run cp1252, which can't encode
# glyphs like the play triangle or check mark (the rest of danvas does the same).
DIM, YEL, GRN, RED, RST = "\033[2m", "\033[33m", "\033[32m", "\033[31m", "\033[0m"
_TAG = {
    "queued": f"{DIM}..  queued{RST}",
    "start":  f"{YEL}>>  start {RST}",
    "done":   f"{GRN}OK  done  {RST}",
    "error":  f"{RED}XX  error {RST}",
}


@canvas.on_dispatch
def trace(e):
    dur = f"  {e['dur_ms']:.0f}ms" if "dur_ms" in e else ""
    err = f"   !! {e['error']}" if e["phase"] == "error" else ""
    indent = "    " * e.get("depth", 0)            # nesting -> indentation
    print(f"[trace {e['trace']:>2}] {indent}{_TAG[e['phase']]} "
          f"{e['handler']}  ({e['mode']}){dur}{err}")


status = canvas.label("status", value="click a button or drag the slider")


# -- project functions the handlers call (these show up nested in the trace) --
def validate(v):
    return v >= 0


def transform(v):
    return v * 2


def compute(v):
    if validate(v):              # depth 2 under the handler
        return transform(v)      # depth 2
    return 0


# -- an inline handler that calls into the pipeline above ---------------------
pipeline = canvas.button("run-pipeline", text="Run pipeline", below=status)


@pipeline.on_click
def run_pipeline():
    result = compute(21)         # compute -> validate -> transform, nested
    status.update(f"pipeline -> {result}")


# -- two threaded handlers, each doing nested work, running concurrently ------
parallel = canvas.button("run-parallel", text="Run parallel", right_of=pipeline)


def fetch(name, secs):
    time.sleep(secs)
    return name


@parallel.on_click(threaded=True)
def fetch_a():
    status.update("parallel: A + B ...")
    fetch("A", 0.6)
    status.update("parallel: A done")


@parallel.on_click(threaded=True)
def fetch_b():
    fetch("B", 0.5)
    status.update("parallel: B done")


# -- a handler whose nested call raises (red error) ---------------------------
boom = canvas.button("boom", text="Boom", right_of=parallel)


def risky():
    raise RuntimeError("intentional demo error")


@boom.on_click
def explode():
    status.update("boom: about to raise ...")
    risky()                      # the error happens one level down


# -- a slider whose handler clamps via a nested helper ------------------------
def clamp(v, lo, hi):
    return max(lo, min(hi, v))


speed = canvas.slider("speed", min=0, max=100, default=20, below=pipeline)


@speed.on_change
def on_speed(v):
    status.update(f"speed = {clamp(v, 0, 100)}")


# -- a long-lived background worker: shows up in the panel's "live threads" ----
@canvas.background
def heartbeat():
    while True:
        time.sleep(1.0)             # just stays alive, like a sensor/telemetry loop


if __name__ == "__main__":
    # An Inspector — click its "Trace" button to launch the live dispatch-trace
    # panel beside it. (You can also call canvas.trace() directly.) trace_calls()
    # here just turns on deep tracing up front so the terminal tap shows nesting
    # immediately; the Trace button turns it on too.
    canvas.inspector(below=speed, w=480, h=300)
    canvas.trace_calls()
    print("danvas dispatch-trace demo — click/drag in the browser. Click the\n"
          "Inspector's 'Trace' button to open the live trace panel; watch the\n"
          "indented call tree there (and in this terminal).\n")
    canvas.serve(port=8000)
