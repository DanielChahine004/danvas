"""Quantify the danvas wire: startup, interaction round-trip latency, JSON +
video throughput (with conflation behavior), and broker memory -- the numbers
the README's "measured on the wire" table comes from.

A raw-frame probe plays the browser role and the Rust conformance target the
owner role, all over loopback. Build both first:

    cargo build --release --manifest-path broker/Cargo.toml
    cargo build --example conformance_target --manifest-path danvas-source/Cargo.toml
    python scripts/bench_wire.py
"""
import asyncio, json, os, socket, statistics, subprocess, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BROKER = os.path.join(ROOT, "broker/target/release/danvasd.exe")
RUST_T = os.path.join(ROOT, "danvas-source/target/debug/examples/conformance_target.exe")

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

async def main():
    from websockets.asyncio.client import connect
    port = free_port()

    # -- startup: spawn -> port open
    t0 = time.perf_counter()
    broker = subprocess.Popen([BROKER, "--port", str(port)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
        except OSError:
            time.sleep(0.005)
    print(f"broker spawn -> port open: {(time.perf_counter()-t0)*1000:.0f} ms")

    target = subprocess.Popen([RUST_T, str(port)], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)

    async with connect(f"ws://127.0.0.1:{port}/ws", max_size=None, max_queue=None) as ws:
        # resolve panel ids from replay
        ids = {}
        end = time.monotonic() + 15
        while time.monotonic() < end and not {"sld", "lbl", "cam", "ctl"} <= ids.keys():
            raw = await asyncio.wait_for(ws.recv(), timeout=end - time.monotonic())
            if isinstance(raw, bytes): continue
            m = json.loads(raw)
            if m.get("type") == "register":
                ids[m.get("name") or m["id"].split(":")[-1]] = m["id"]

        # -- round-trip latency: input -> owner handler -> update echo
        lats = []
        for i in range(300):
            t = time.perf_counter()
            await ws.send(json.dumps({"type": "input", "id": ids["sld"],
                                      "payload": {"value": i % 100}}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                if isinstance(raw, bytes): continue
                m = json.loads(raw)
                if (m.get("type") == "update" and m.get("id") == ids["lbl"]
                        and (m.get("payload") or {}).get("post") == f"v={i % 100}"):
                    lats.append((time.perf_counter() - t) * 1000)
                    break
        lats.sort()
        print(f"input->handler->echo round trip (probe->broker->rust->broker->probe):")
        print(f"  median {statistics.median(lats):.2f} ms, p95 {lats[int(len(lats)*0.95)]:.2f} ms, min {lats[0]:.2f} ms")

    # -- JSON update throughput: raw source floods, probe counts
    async with connect(f"ws://127.0.0.1:{port}/ws?source=1&label=flood",
                       max_size=None) as src, \
               connect(f"ws://127.0.0.1:{port}/ws", max_size=None,
                       max_queue=None) as probe:
        await src.send(json.dumps({"type": "register", "id": "f", "name": "f",
                                   "component": "React", "props": {"source": "x"}}))
        # drain replay on probe briefly
        try:
            while True:
                await asyncio.wait_for(probe.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            pass
        N = 20000
        async def produce():
            t0 = time.perf_counter()
            for i in range(N):
                await src.send(json.dumps({"type": "update", "id": "f",
                                           "payload": {"post": i}}))
            return time.perf_counter() - t0
        async def consume():
            got = 0
            t0 = time.perf_counter()
            while True:
                raw = await asyncio.wait_for(probe.recv(), timeout=20)
                if isinstance(raw, bytes): continue
                m = json.loads(raw)
                if m.get("type") == "update" and m.get("id", "").endswith(":f"):
                    got += 1
                    if (m.get("payload") or {}).get("post") == N - 1:
                        return got, time.perf_counter() - t0
        pt, (got, dt) = await asyncio.gather(produce(), consume())
        print(f"JSON updates: source ingest {N/pt:,.0f} msg/s; browser saw the "
              f"final value after {dt:.2f}s having received {got:,} frames "
              f"({got/dt:,.0f} msg/s delivered; conflation dropped "
              f"{100*(1-got/N):.0f}% stale)")

        # -- binary video throughput: 100 KB frames
        frame = bytes([1, 1]) + b"f" + os.urandom(100_000)
        M = 500
        async def vproduce():
            for _ in range(M):
                await src.send(frame)
        async def vconsume():
            got = 0
            t0 = time.perf_counter()
            while got < M:
                raw = await asyncio.wait_for(probe.recv(), timeout=30)
                if isinstance(raw, bytes) and len(raw) > 50_000:
                    got += 1
            return time.perf_counter() - t0
        _, dt = await asyncio.gather(vproduce(), vconsume())
        mb = M * len(frame) / 1048576
        print(f"video envelopes (100 KB JPEG-sized): {M/dt:,.0f} fps, {mb/dt:,.0f} MB/s relayed")

    # -- broker memory under that load
    out = subprocess.check_output(["tasklist", "/FI", f"PID eq {broker.pid}", "/FO", "CSV"]).decode()
    rss = out.strip().splitlines()[-1].split('","')[-1].replace('"', '').strip()
    print(f"broker RSS after load: {rss}")

    target.kill(); broker.kill()

asyncio.run(main())
