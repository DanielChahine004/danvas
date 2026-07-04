"""Benchmark a hub's fan-out: throughput + tail latency, both hubs, one switch.

The Rust broker's whole thesis was faster fan-out and lower jitter than the
Python hub. This measures it — not a unit test (it isn't run by the suite),
a stopwatch. Same DANVAS_HUB_CMD contract as the conformance harness:

    python tests/benchmark_hub.py                                    # Python hub
    DANVAS_HUB_CMD="broker/target/release/danvasd.exe|--port|{port}" \\
        python tests/benchmark_hub.py                                # danvasd

A source registers one panel and streams timestamped updates as fast as it
can for a fixed window; N browsers subscribe; we measure how many frames each
browser actually received (fan-out throughput) and the source->browser
latency distribution (p50/p99/max). Tune with --viewers / --seconds / --rate.
"""

import argparse
import asyncio
import json
import os
import socket
import statistics
import subprocess
import sys
import time

from websockets.asyncio.client import connect as ws_connect


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _spawn(port):
    tpl = os.environ.get("DANVAS_HUB_CMD")
    if tpl:
        cmd = [x.format(port=port, password="") for x in tpl.split("|")]
        label = "danvasd"
    else:
        cmd = [sys.executable, "-m", "danvas.merge", "--port", str(port),
               "--no-open"]
        label = "python-hub"
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    end = time.time() + 20
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return proc, label
        except OSError:
            if proc.poll() is not None:
                raise RuntimeError("hub exited early")
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("hub never opened its port")


async def _run(port, viewers, seconds, rate):
    uri = f"ws://127.0.0.1:{port}/ws"
    src = await ws_connect(f"{uri}?source=1&label=bench", max_size=None)
    # wait for welcome
    while json.loads(await src.recv())["type"] != "welcome":
        pass
    await src.send(json.dumps({"type": "register", "id": "p", "name": "p",
                               "component": "React", "props": {}}))

    stats = [{"n": 0, "lat": []} for _ in range(viewers)]
    stop = asyncio.Event()

    async def viewer(i):
        ws = await ws_connect(uri, max_size=None)
        nsid = None
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), 1.0)
            except asyncio.TimeoutError:
                continue
            if isinstance(raw, bytes):
                continue
            m = json.loads(raw)
            if m.get("type") == "register" and m.get("name") == "p":
                nsid = m["id"]
            elif (m.get("type") == "update" and m.get("id") == nsid
                    and "t" in (m.get("payload") or {})):
                stats[i]["n"] += 1
                stats[i]["lat"].append(time.perf_counter()
                                       - m["payload"]["t"])
        await ws.close()

    tasks = [asyncio.create_task(viewer(i)) for i in range(viewers)]
    await asyncio.sleep(1.0)   # let viewers attach + learn the panel id

    sent = 0
    interval = 1.0 / rate if rate else 0
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        await src.send(json.dumps({"type": "update", "id": "p",
                                   "payload": {"t": time.perf_counter(),
                                               "value": sent}}))
        sent += 1
        if interval:
            await asyncio.sleep(interval)
    await asyncio.sleep(1.0)   # drain in-flight
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    await src.close()
    return sent, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--viewers", type=int, default=20)
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--rate", type=float, default=0,
                    help="source updates/sec (0 = unthrottled, max throughput)")
    args = ap.parse_args()

    port = _free_port()
    proc, label = _spawn(port)
    try:
        sent, stats = asyncio.run(
            _run(port, args.viewers, args.seconds, args.rate))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    total_recv = sum(s["n"] for s in stats)
    all_lat = [x for s in stats for x in s["lat"]]
    fanout = sent * args.viewers
    print(f"\n=== {label} — {args.viewers} viewers, {args.seconds}s, "
          f"rate={'max' if not args.rate else args.rate} ===")
    print(f"source sent:        {sent:>8}  ({sent/args.seconds:,.0f}/s)")
    print(f"frames delivered:   {total_recv:>8}  of {fanout} "
          f"({100*total_recv/fanout:.0f}% — coalescing drops the rest)")
    print(f"fan-out throughput: {total_recv/args.seconds:>10,.0f} frames/s")
    if all_lat:
        ms = sorted(x * 1000 for x in all_lat)
        p = lambda q: ms[min(len(ms) - 1, int(len(ms) * q))]
        print(f"latency  p50 {p(0.5):.2f}ms  p99 {p(0.99):.2f}ms  "
              f"max {ms[-1]:.2f}ms  (mean {statistics.mean(ms):.2f}ms)")


if __name__ == "__main__":
    main()
