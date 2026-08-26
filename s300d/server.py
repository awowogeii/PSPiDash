"""Local WebSocket/static server feeding the browser UI.

The ECU source runs in a plain thread (its ``frames()`` generator blocks);
it only ever writes the latest frame into ``Hub``. The asyncio broadcast loop
reads that at its own rate, so a slow or dead browser can never apply
backpressure to the ECU poll loop: a send that does not complete within
``client_send_timeout_s`` gets the client dropped.
"""
import asyncio
import json
import logging
import os
import threading
import time

from aiohttp import WSMsgType, web

from s300d import alarms as alarms_mod

log = logging.getLogger("s300d.server")

HUB = web.AppKey("hub", object)
CFG = web.AppKey("cfg", dict)
CLIENTS = web.AppKey("clients", set)

DEFAULT_SERVER = {"host": "127.0.0.1", "port": 8080, "broadcast_hz": 30,
                  "stale_after_s": 0.5, "client_send_timeout_s": 0.5}
ALLOWED_CMDS = ("ack_alarms", "release_bt", "resume_bt")
# Exact public gauge keys (every mapped channel + derived values); the
# canonical list lives next to derive(). "wideband_lambda" is added only
# when afr_available.
PUBLIC_KEYS = alarms_mod.PUBLIC_KEYS


class Hub:
    """Latest-value store shared between the source thread and the server."""

    def __init__(self, source, alarm_rules, shift_stages=None, clock=time.monotonic):
        self.source = source
        self.alarm_rules = alarm_rules or {}
        self.shift_stages = shift_stages
        self.clock = clock
        self.lock = threading.Lock()
        self.d = {}
        self.alarms = []
        self.afr_available = False
        self.received_at = None
        self.source_error = None
        self.last_values = {}
        self.engine = None
        self._channel_list = None
        self._thread = None
        self._stop = False

    # -- source thread ------------------------------------------------------

    def start(self):
        self._thread = threading.Thread(target=self._run, name="s300-source", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self):
        self._stop = True
        self.source.close()

    def _run(self):
        try:
            for frame in self.source.frames():
                if self._stop:
                    break
                self.ingest(frame)
        except Exception as exc:  # the source must never take the server down
            log.exception("source thread died: %s", exc)
            with self.lock:
                self.source_error = str(exc)
        finally:
            log.info("source thread finished")

    def ingest(self, frame):
        """Convert + evaluate one frame. Called from the source thread (or tests)."""
        channel_list = self.source.channel_list
        if self.engine is None or channel_list != self._channel_list:
            self._channel_list = list(channel_list) if channel_list else None
            self.engine = alarms_mod.AlarmEngine(self.alarm_rules, channel_list)
            self.afr_available = alarms_mod.afr_available(channel_list)
        now = self.clock()
        d = alarms_mod.derive(frame.values, channel_list, self.shift_stages)
        active = self.engine.update(d, now)
        with self.lock:
            self.d = d
            self.alarms = active
            self.last_values = frame.values
            self.received_at = now

    def reload(self, conf):
        """Hot-apply a new config dict: alarm rules, shift light, scaling.

        Alarm/shift changes take effect on the next frame. Scaling changes
        need a fresh offset table, so the source is bounced (release/resume)
        and rebuilds it during its next handshake.
        """
        self.alarm_rules = conf.get("alarms") or {}
        self.shift_stages = conf.get("shift_light")
        with self.lock:
            self.engine = None  # rebuilt on next ingest
        new_scaling = conf.get("scaling_overrides") or {}
        if getattr(self.source, "scaling_overrides", None) != new_scaling:
            self.source.scaling_overrides = new_scaling
            self.source.release()
            self.source.resume()
        log.info("config reloaded: %d alarm rules", len(self.alarm_rules))

    # -- snapshot for broadcast ---------------------------------------------

    def state_name(self):
        if self.source_error and self.source.state.value == "DISCONNECTED":
            return "ERROR"
        return self.source.state.value

    def snapshot(self, stale_after):
        now = self.clock()
        with self.lock:
            stale = self.received_at is None or (now - self.received_at) > stale_after
            keys = PUBLIC_KEYS + (("wideband_lambda",) if self.afr_available else ())
            return {"t": now, "state": self.state_name(),
                    "stale": bool(stale) or self.state_name() != "STREAMING",
                    "afr_available": self.afr_available,
                    "d": {k: self.d.get(k) for k in keys} if self.d else {},
                    "a": self.alarms}

    # -- commands from the UI -----------------------------------------------

    def command(self, cmd):
        if cmd == "ack_alarms":
            if self.engine is not None:
                self.engine.ack()
                with self.lock:
                    self.alarms = self.engine.active()
        elif cmd == "release_bt":
            self.source.release()
        elif cmd == "resume_bt":
            self.source.resume()
        else:
            raise ValueError("unknown command")


# --- aiohttp app -----------------------------------------------------------

def make_app(hub, server_cfg=None, ui_dir="ui"):
    cfg = dict(DEFAULT_SERVER)
    cfg.update(server_cfg or {})
    app = web.Application()
    app[HUB] = hub
    app[CFG] = cfg
    app[CLIENTS] = set()
    app.router.add_get("/ws", ws_handler)
    if os.path.isdir(ui_dir):
        app.router.add_get("/", lambda r: web.FileResponse(os.path.join(ui_dir, "index.html")))
        app.router.add_static("/", ui_dir)
    else:
        log.debug("no ui directory %r; serving WebSocket only (native s300ui is the UI)", ui_dir)
    app.cleanup_ctx.append(_broadcast_ctx)
    return app


async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=5.0)
    await ws.prepare(request)
    clients = request.app[CLIENTS]
    hub = request.app[HUB]
    clients.add(ws)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                body = json.loads(msg.data)
                cmd = body.get("cmd") if isinstance(body, dict) else None
                if cmd not in ALLOWED_CMDS or len(body) != 1:
                    raise ValueError("unknown command")
                hub.command(cmd)
                await ws.send_json({"ok": cmd})
            except (ValueError, AttributeError) as exc:
                await ws.send_json({"error": "rejected", "detail": str(exc)})
    finally:
        clients.discard(ws)
    return ws


async def _broadcast_ctx(app):
    task = asyncio.create_task(broadcast_loop(app))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    for ws in list(app[CLIENTS]):
        await ws.close()


async def broadcast_loop(app):
    hub, cfg, clients = app[HUB], app[CFG], app[CLIENTS]
    period = 1.0 / float(cfg["broadcast_hz"])
    timeout = float(cfg["client_send_timeout_s"])
    stale_after = float(cfg["stale_after_s"])
    while True:
        started = time.monotonic()
        if clients:
            payload = json.dumps(hub.snapshot(stale_after))
            for ws in list(clients):
                try:
                    await asyncio.wait_for(ws.send_str(payload), timeout)
                except (asyncio.TimeoutError, ConnectionResetError, RuntimeError) as exc:
                    log.warning("dropping slow/dead WebSocket client: %s", exc)
                    clients.discard(ws)
                    asyncio.create_task(_close_quietly(ws))
        await asyncio.sleep(max(0.0, period - (time.monotonic() - started)))


async def _close_quietly(ws):
    try:
        await asyncio.wait_for(ws.close(), 1.0)
    except Exception:
        pass


def run(hub, server_cfg=None, ui_dir="ui", extra_sites=()):
    """Serve the data app on loopback plus any (app, host, port) extras, forever."""
    cfg = dict(DEFAULT_SERVER)
    cfg.update(server_cfg or {})
    app = make_app(hub, cfg, ui_dir)

    async def serve():
        runners = []

        async def bind(a, host, port, retry):
            runner = web.AppRunner(a, access_log=None)
            await runner.setup()
            runners.append(runner)
            while True:
                try:
                    await web.TCPSite(runner, host, port).start()
                    log.info("serving on http://%s:%d", host, port)
                    return
                except OSError as exc:
                    if not retry:
                        log.error("cannot bind %s:%d: %s", host, port, exc)
                        return
                    # e.g. hotspot interface not up yet; keep trying so the
                    # settings page appears whenever the hotspot does
                    log.warning("cannot bind %s:%d (%s); retrying in 5s", host, port, exc)
                    await asyncio.sleep(5)

        await bind(app, cfg["host"], int(cfg["port"]), retry=False)
        tasks = [asyncio.create_task(bind(a, h, p, retry=True)) for a, h, p in extra_sites]
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            for t in tasks:
                t.cancel()
            for r in runners:
                await r.cleanup()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
