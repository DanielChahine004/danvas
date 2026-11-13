"""danvas.webserial outside a browser: lines split across chunks, and the local port."""
from danvas.webserial import Serial


def test_lines_split_across_chunks():
    dev, lines, chunks = Serial(), [], []
    dev.on_line(lines.append)
    dev.on_data(chunks.append)
    for part in [b"51", b"2,30\r\nhel", b"lo\n", b"partial"]:
        dev._feed(part)
    assert lines == ["512,30", "hello"]
    assert len(chunks) == 4


def test_open_and_close_are_reported():
    dev, seen = Serial(), []
    dev.on_open(lambda info: seen.append(("open", info)))
    dev.on_close(lambda why: seen.append(("closed", why)))
    dev._state(True, "COM9")
    dev._state(False, "unplugged")
    assert seen == [("open", "COM9"), ("closed", "unplugged")]
    assert not dev.is_open


def test_no_port_outside_a_browser_says_so():
    dev, why = Serial(), []
    dev.on_close(why.append)
    dev.open()
    assert why and ("port" in why[0] or "pyserial" in why[0])
