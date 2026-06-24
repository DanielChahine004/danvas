"""canvas.describe() — the text-state inventory half of the LLM feedback pair."""

import danvas


def test_describe_lists_panels_with_state():
    canvas = danvas.Canvas()
    servo = canvas.slider("servo_1", min=0, max=180, default=90)
    canvas.label("status", "idle", below=servo)

    rows = canvas.describe()
    by_name = {r["name"]: r for r in rows}

    assert set(by_name) >= {"servo_1", "status"}
    assert by_name["servo_1"]["type"] == "Slider"
    assert by_name["servo_1"]["value"] == "90"      # live value, length-capped repr
    assert by_name["status"]["value"] == "'idle'"
    assert by_name["servo_1"]["visible"] is True


def test_describe_includes_arrows():
    canvas = danvas.Canvas()
    a = canvas.label("a", "a")
    b = canvas.label("b", "b")
    canvas.connect(a, b, text="x2")

    arrows = [r for r in canvas.describe() if r["type"] == "Arrow"]
    assert len(arrows) == 1
    assert arrows[0]["label"] == "x2"
