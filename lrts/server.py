"""Live match server (M1c) and replay file server.

    python -m lrts.server specs/rush.toml specs/turtle.toml --seed 3 [--port 8765] [--tps 20]
    open http://localhost:8765/

Design points (real-time-systems track, D27):
  * The sim never sees a clock. This loop owns pacing: one tick every 1/tps seconds,
    with a deadline that advances by a fixed step, so slow ticks do not accumulate drift.
  * Static data (map, specs, kinds) is sent once in `hello`; each tick sends only entities.
  * Backpressure: every spectator has a one-slot "latest message" mailbox. A slow client
    skips frames (each frame is a full state, so skipping is safe); it can never block the
    match or the other spectators.
  * Every live match is also recorded, and written to replays/ when it ends.
"""
import argparse
import asyncio
import contextlib
import json
import mimetypes
import time
from pathlib import Path

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from .agents import load_spec
from .match import Match

ROOT = Path(__file__).resolve().parent.parent
VIEWER = ROOT / "viewer"
REPLAYS = ROOT / "replays"


class Spectator:
    def __init__(self, ws):
        self.ws = ws
        self.mailbox = None          # latest frame message (overwritten, never queued)
        self.pending_notes = {}      # agent notes of frames we skipped: merged, never lost
        self.priority = []           # messages that must not be dropped (hello, end)
        self.wake = asyncio.Event()
        self.dropped = 0
        self.sent = 0
        self.bytes = 0

    def offer_frame(self, msg, notes=None):
        if self.mailbox is not None:
            self.dropped += 1
        self.mailbox = msg
        if notes:
            self.pending_notes.update(notes)
        self.wake.set()

    def offer_reliable(self, msg):
        self.priority.append(msg)
        self.wake.set()

    async def pump(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            while self.priority or self.mailbox is not None:
                msg = self.priority.pop(0) if self.priority else self.mailbox
                if msg is self.mailbox:
                    self.mailbox = None
                    if self.pending_notes:       # splice the merged notes into the frame JSON
                        msg = msg[:-1] + ',"notes":' + json.dumps(self.pending_notes) + "}"
                        self.pending_notes = {}
                await self.ws.send(msg)
                self.sent += 1
                self.bytes += len(msg)


class Room:
    def __init__(self, spec0, spec1, seed, tps):
        self.match = Match(spec0, spec1, seed, record=True)
        self.tps = tps
        self.paused = False
        self.step_once = False
        self.spectators = set()
        self.hello = json.dumps({"type": "hello", **self.match.header(), "tps": tps})
        self.last_frame_msg = None
        self.end_msg = None
        self.replay_path = None

    def command(self, d):
        c = d.get("cmd")
        if c == "pause":
            self.paused = True
        elif c == "play":
            self.paused = False
        elif c == "step":
            self.step_once = True
        elif c == "speed" and isinstance(d.get("v"), (int, float)):
            self.tps = max(1, min(2000, float(d["v"])))

    def broadcast_frame(self, fr, notes):
        msg = json.dumps({"type": "frame", **fr, "paused": self.paused, "tps": self.tps},
                         separators=(",", ":"))
        self.last_frame_msg = msg
        note = {fr["t"]: notes} if any(notes) else None
        for s in list(self.spectators):
            s.offer_frame(msg, note)

    async def run(self):
        m = self.match
        next_deadline = time.perf_counter()
        while not m.finished:
            if self.paused and not self.step_once:
                await asyncio.sleep(0.05)
                next_deadline = time.perf_counter()
                continue
            self.step_once = False
            fr, notes = m.tick_once()
            self.broadcast_frame(fr, notes)
            next_deadline += 1.0 / self.tps
            delay = next_deadline - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(0)       # yield so websockets can run even flat out
                if delay < -1:
                    next_deadline = time.perf_counter()   # far behind: do not try to catch up
        REPLAYS.mkdir(exist_ok=True)
        names = f"{m.specs[0]['name']}-vs-{m.specs[1]['name']}-seed{m.seed}"
        self.replay_path = REPLAYS / f"{names}.json"
        self.replay_path.write_text(json.dumps(m.replay(), separators=(",", ":")))
        self.end_msg = json.dumps({"type": "end", "result": m.summary(), "replay": self.replay_path.name})
        for s in list(self.spectators):
            s.offer_reliable(self.end_msg)


def _http(path):
    if path in ("", "/"):
        path = "/index.html"
    if path == "/replays.json":
        names = sorted(p.name for p in REPLAYS.glob("*.json")) if REPLAYS.exists() else []
        body = json.dumps(names).encode()
        headers = Headers([("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        return Response(200, "OK", headers, body)
    if path.startswith("/replays/"):
        f = (REPLAYS / path[len("/replays/"):]).resolve()
        base = REPLAYS.resolve()
    else:
        f = (VIEWER / path.lstrip("/")).resolve()
        base = VIEWER.resolve()
    if base not in f.parents or not f.is_file():        # also blocks ../ traversal
        return Response(404, "Not Found", Headers([("Content-Type", "text/plain")]), b"not found")
    body = f.read_bytes()
    ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
    return Response(200, "OK", Headers([("Content-Type", ctype), ("Content-Length", str(len(body)))]), body)


async def serve_room(room, host, port):
    async def handler(ws):
        spec = Spectator(ws)
        spec.offer_reliable(room.hello)
        if room.last_frame_msg:
            spec.offer_frame(room.last_frame_msg)
        if room.end_msg:
            spec.offer_reliable(room.end_msg)
        room.spectators.add(spec)
        pump = asyncio.create_task(spec.pump())
        try:
            async for raw in ws:
                with contextlib.suppress(ValueError, AttributeError):   # garbage from a client is ignored
                    room.command(json.loads(raw))
        finally:
            room.spectators.discard(spec)
            pump.cancel()

    def process_request(connection, request):
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None
        return _http(request.path.split("?")[0])

    async with serve(handler, host, port, process_request=process_request, max_size=2 ** 16):
        await room.run()
        await asyncio.sleep(3600)       # keep serving the finished match / replays


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec0")
    ap.add_argument("spec1")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")   # local only: there is no auth (ejaj-boj had CORS * and none)
    ap.add_argument("--tps", type=float, default=20)
    a = ap.parse_args()
    room = Room(load_spec(a.spec0), load_spec(a.spec1), a.seed, a.tps)
    print(f"http://{a.host}:{a.port}/  ({room.match.specs[0]['name']} vs {room.match.specs[1]['name']}, seed {a.seed})")
    asyncio.run(serve_room(room, a.host, a.port))


if __name__ == "__main__":
    main()
