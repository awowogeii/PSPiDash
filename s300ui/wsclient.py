"""Background WebSocket client: keeps the latest daemon message, reconnects forever."""
import asyncio
import json
import logging
import threading
import time

import aiohttp

log = logging.getLogger("s300ui.ws")


class DaemonClient:
    def __init__(self, url="ws://127.0.0.1:8080/ws"):
        self.url = url
        self.latest = None
        self.received_at = None
        self.connected = False
        self._cmds = []
        self._lock = threading.Lock()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="s300ui-ws", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True

    def send(self, cmd):
        with self._lock:
            self._cmds.append(cmd)

    def message(self):
        with self._lock:
            return self.latest

    def _run(self):
        asyncio.run(self._loop())

    async def _loop(self):
        delay = 0.5
        while not self._stop:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(self.url, heartbeat=5.0, timeout=3.0) as ws:
                        self.connected = True
                        delay = 0.5
                        log.info("connected to %s", self.url)
                        while not self._stop:
                            with self._lock:
                                pending, self._cmds = self._cmds, []
                            for cmd in pending:
                                await ws.send_json({"cmd": cmd})
                            msg = await ws.receive(timeout=2.0)
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                if "d" in data:
                                    with self._lock:
                                        self.latest = data
                                        self.received_at = time.monotonic()
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                log.warning("daemon link: %s", exc)
            self.connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)
