"""Live server: streaming, controls, backpressure, replay written, static files."""
import asyncio
import contextlib
import json
import urllib.request

from websockets.asyncio.client import connect

from lrts import server
from lrts.agents import spec_from_params


def _room(tps=2000, seed=1):
    a = spec_from_params("a", {"attack_size": 4, "barracks_at": 40})
    b = spec_from_params("b", {})
    return server.Room(a, b, seed, tps)


async def _with_server(room, fn, tmp_path):
    server.REPLAYS = tmp_path
    port = 18765
    task = asyncio.create_task(server.serve_room(room, "127.0.0.1", port))
    await asyncio.sleep(0.2)
    try:
        return await fn(port)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def test_live_stream_end_and_replay(tmp_path):
    room = _room()

    async def go(port):
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            types, last_t = [], 0
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 30))
                types.append(m["type"])
                if m["type"] == "frame":
                    assert m["t"] >= last_t           # frames never go backwards
                    last_t = m["t"]
                if m["type"] == "end":
                    return types, m
    types, end = asyncio.run(_with_server(room, go, tmp_path))
    assert types[0] == "hello" and types[-1] == "end" and "frame" in types
    rep = json.loads((tmp_path / end["replay"]).read_text())
    assert rep["header"]["seed"] == 1 and rep["header"]["rules_hash"] and len(rep["frames"]) == end["result"]["ticks"]
    assert rep["result"]["hash"] == end["result"]["hash"]
    assert rep["header"]["speed"]["worker"] == 4          # the viewer glides units over this many ticks


def test_pause_step_speed(tmp_path):
    room = _room(tps=50)

    async def go(port):
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            await ws.recv()                                   # hello
            await ws.send(json.dumps({"cmd": "pause"}))
            await asyncio.sleep(0.3)
            t0 = room.match.world.tick
            await asyncio.sleep(0.3)
            assert room.match.world.tick == t0                # really paused
            await ws.send(json.dumps({"cmd": "step"}))
            await asyncio.sleep(0.3)
            assert room.match.world.tick == t0 + 1            # exactly one tick
            await ws.send(json.dumps({"cmd": "speed", "v": 100000}))
            await ws.send("not json {{{")                     # garbage must not kill the connection
            await ws.send(json.dumps({"cmd": "play"}))
            await asyncio.sleep(0.5)
            assert room.tps == 2000                           # clamped
            assert room.match.world.tick > t0 + 1
    asyncio.run(_with_server(room, go, tmp_path))


def test_slow_spectator_does_not_block_match(tmp_path):
    room = _room(tps=2000)

    async def go(port):
        slow = await connect(f"ws://127.0.0.1:{port}/ws")     # never reads
        async with connect(f"ws://127.0.0.1:{port}/ws") as fast:
            while True:
                m = json.loads(await asyncio.wait_for(fast.recv(), 30))
                if m["type"] == "end":
                    break
        await slow.close()
        assert room.match.finished
    asyncio.run(_with_server(room, go, tmp_path))


def test_late_joiner_gets_hello_and_state(tmp_path):
    room = _room(tps=2000)

    async def go(port):
        await asyncio.wait_for(room.match_done.wait(), 60) if hasattr(room, "match_done") else await asyncio.sleep(8)
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            got = [json.loads(await asyncio.wait_for(ws.recv(), 5))["type"] for _ in range(3)]
            assert got[0] == "hello" and "end" in got
    asyncio.run(_with_server(room, go, tmp_path))


def test_http_static_and_traversal(tmp_path):
    room = _room(tps=2000)

    async def go(port):
        loop = asyncio.get_running_loop()
        def get(p):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{p}", timeout=5) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, b""
        s, body = await loop.run_in_executor(None, get, "/")
        assert s == 200 and b"deep-strategy" in body
        s, _ = await loop.run_in_executor(None, get, "/../lrts/world.py")
        assert s == 404
        s, _ = await loop.run_in_executor(None, get, "/replays/../../README.md")
        assert s == 404
    asyncio.run(_with_server(room, go, tmp_path))
