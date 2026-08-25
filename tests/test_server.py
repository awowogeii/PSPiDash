import asyncio
import json
import socket
import threading
import time

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from s300d import server as srv
from s300d.client import Frame, State

CHANNELS = [(0x0100, 0x83), (0x0110, 0x85), (0x0170, 0x85), (0x0160, 0x50), (0x0180, 0x59)]
VALUES = {"RPM": 3420.0, "MAP": 42.1, "BarometricPressure": 101.4, "ECT": 190.4,
          "BatteryVoltage": 14.1}


class FakeSource:
    """Yields frames until ``die`` is set, then ends like an exhausted replay."""

    def __init__(self, frames_before_death=None, raise_error=False):
        self.state = State.DISCONNECTED
        self.channel_list = CHANNELS
        self.n = frames_before_death
        self.raise_error = raise_error
        self.die = threading.Event()
        self.released = False
        self.resumed = False
        self.closed = False

    def frames(self):
        self.state = State.STREAMING
        i = 0
        while not self.die.is_set() and (self.n is None or i < self.n):
            yield Frame(time.monotonic(), b"", VALUES)
            i += 1
            time.sleep(0.01)
        if self.raise_error:
            self.state = State.DISCONNECTED
            raise RuntimeError("boom")
        self.state = State.DISCONNECTED

    def release(self):
        self.released = True

    def resume(self):
        self.resumed = True

    def close(self):
        self.closed = True
        self.die.set()


CFG = {"broadcast_hz": 50, "stale_after_s": 0.1, "client_send_timeout_s": 0.2}


def run_async(coro):
    return asyncio.run(coro)


async def ws_client(hub, cfg=CFG):
    app = srv.make_app(hub, cfg, ui_dir="/nonexistent-ui")
    client = TestClient(TestServer(app))
    await client.start_server()
    ws = await client.ws_connect("/ws")
    return client, ws


async def next_json(ws):
    msg = await asyncio.wait_for(ws.receive(), 2.0)
    assert msg.type == WSMsgType.TEXT
    return json.loads(msg.data)


# --- Hub (sync) -----------------------------------------------------------------

def test_hub_ingest_builds_message_shape():
    src = FakeSource()
    hub = srv.Hub(src, {}, {"amber": 7400, "red": 7900, "flash": 8100})
    src.state = State.STREAMING
    hub.ingest(Frame(1.0, b"", VALUES))
    snap = hub.snapshot(stale_after=1.0)
    assert set(snap) == {"t", "state", "stale", "afr_available", "d", "a"}
    assert snap["state"] == "STREAMING" and snap["stale"] is False
    assert snap["afr_available"] is False and snap["a"] == []
    d = snap["d"]
    assert d["rpm"] == 3420 and d["ect_c"] == pytest.approx(88.0)
    assert d["boost_psi"] == pytest.approx((42.1 - 101.4) * 0.145038)
    assert d["vtec"] is False and d["shift_stage"] == 0
    assert set(d) == set(srv.PUBLIC_KEYS)  # every channel published; absent ones None
    assert d["speed_kph"] is None and d["knock_count"] is None
    assert "wideband_lambda" not in d


def test_hub_marks_stale_when_no_fresh_frame():
    src = FakeSource()
    src.state = State.STREAMING
    now = [100.0]
    hub = srv.Hub(src, {}, clock=lambda: now[0])
    hub.ingest(Frame(1.0, b"", VALUES))
    assert hub.snapshot(0.5)["stale"] is False
    now[0] += 0.6
    assert hub.snapshot(0.5)["stale"] is True


def test_hub_rebuilds_engine_when_channel_list_changes():
    src = FakeSource()
    hub = srv.Hub(src, {})
    hub.ingest(Frame(1.0, b"", VALUES))
    assert hub.afr_available is False
    src.channel_list = CHANNELS + [(0x0329, 0x9E)]
    hub.ingest(Frame(2.0, b"", VALUES))
    assert hub.afr_available is True
    assert "wideband_lambda" in hub.snapshot(1.0)["d"]


def test_hub_commands_route_to_source():
    src = FakeSource()
    hub = srv.Hub(src, {})
    hub.command("release_bt")
    hub.command("resume_bt")
    hub.command("ack_alarms")  # engine not built yet; must not raise
    assert src.released and src.resumed
    with pytest.raises(ValueError):
        hub.command("reboot")


# --- WebSocket (async) ----------------------------------------------------------

def test_ws_broadcasts_and_handles_commands():
    async def body():
        src = FakeSource()
        hub = srv.Hub(src, {})
        hub.start()
        client, ws = await ws_client(hub)
        try:
            msg = await next_json(ws)
            assert msg["state"] == "STREAMING" and msg["stale"] is False
            assert msg["d"]["rpm"] == 3420
            await ws.send_json({"cmd": "ack_alarms"})
            # drain broadcasts until the command reply shows up
            for _ in range(20):
                m = await next_json(ws)
                if "ok" in m:
                    assert m == {"ok": "ack_alarms"}
                    break
            else:
                pytest.fail("no ack reply")
            await ws.send_json({"cmd": "release_bt"})
            for _ in range(20):
                if (await next_json(ws)).get("ok") == "release_bt":
                    break
            assert src.released
        finally:
            await ws.close()
            await client.close()
            hub.stop()
    run_async(body())


def test_ws_rejects_unknown_and_malformed_commands():
    async def body():
        src = FakeSource()
        hub = srv.Hub(src, {})
        client, ws = await ws_client(hub, CFG | {"broadcast_hz": 1})
        try:
            for bad in ('{"cmd": "read_dtc"}', '{"cmd": "ack_alarms", "x": 1}', 'nope', '[1]'):
                await ws.send_str(bad)
                for _ in range(5):
                    m = await next_json(ws)
                    if "error" in m:
                        assert m["error"] == "rejected"
                        break
                else:
                    pytest.fail("no rejection for %r" % bad)
        finally:
            await ws.close()
            await client.close()
    run_async(body())


def test_source_death_sets_state_and_stale_without_crashing():
    async def body():
        src = FakeSource()  # streams until we kill it below
        hub = srv.Hub(src, {})
        hub.start()
        client, ws = await ws_client(hub)
        try:
            saw_stream = False
            for _ in range(60):
                m = await next_json(ws)
                if m["state"] == "STREAMING" and not m["stale"]:
                    saw_stream = True
                    src.die.set()  # die only once a live message was observed
                if m["state"] == "DISCONNECTED":
                    assert m["stale"] is True
                    assert m["d"]["rpm"] == 3420  # last value repeated
                    break
            else:
                pytest.fail("never saw DISCONNECTED")
            assert saw_stream
            # server still alive and answering
            await ws.send_json({"cmd": "resume_bt"})
            for _ in range(10):
                if (await next_json(ws)).get("ok") == "resume_bt":
                    break
            else:
                pytest.fail("server stopped responding")
        finally:
            await ws.close()
            await client.close()
            hub.stop()
    run_async(body())


def test_source_exception_reported_as_error_state():
    async def body():
        src = FakeSource(frames_before_death=1, raise_error=True)
        hub = srv.Hub(src, {})
        hub.start()
        client, ws = await ws_client(hub)
        try:
            for _ in range(60):
                m = await next_json(ws)
                if m["state"] == "ERROR":
                    assert m["stale"] is True
                    break
            else:
                pytest.fail("never saw ERROR")
        finally:
            await ws.close()
            await client.close()
            hub.stop()
    run_async(body())


def test_slow_client_is_dropped_and_others_keep_flowing():
    async def body():
        src = FakeSource()
        hub = srv.Hub(src, {})
        hub.start()
        app = srv.make_app(hub, CFG | {"client_send_timeout_s": 0.05}, ui_dir="/none")
        client = TestClient(TestServer(app))
        await client.start_server()
        good = await client.ws_connect("/ws")
        slow = await client.ws_connect("/ws")
        try:
            await next_json(good)
            assert len(app[srv.CLIENTS]) == 2
            # simulate a client whose send never completes
            target = [w for w in app[srv.CLIENTS]][-1]
            target.send_str = lambda payload: asyncio.sleep(10)
            for _ in range(30):
                await next_json(good)
                if len(app[srv.CLIENTS]) == 1:
                    break
            assert len(app[srv.CLIENTS]) == 1
            assert target not in app[srv.CLIENTS]
            await next_json(good)  # healthy client still receives
        finally:
            await good.close()
            await slow.close()
            await client.close()
            hub.stop()
    run_async(body())


def test_bind_loopback_only():
    src = FakeSource()
    hub = srv.Hub(src, {})
    app = srv.make_app(hub, CFG, ui_dir="/none")

    async def body():
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        # accepted on loopback
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
        # refused on any non-loopback address of this host
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))  # no packets sent; just picks an outbound address
            hostname_ip = probe.getsockname()[0]
        finally:
            probe.close()
        if hostname_ip.startswith("127."):
            pytest.skip("host has no non-loopback address to test against")
        with pytest.raises(OSError):
            socket.create_connection((hostname_ip, port), timeout=1)
        await runner.cleanup()
    run_async(body())
