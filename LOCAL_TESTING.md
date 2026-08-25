# Testing on a laptop (Windows / WSL / macOS / Linux)

Nothing here needs the car or a Pi. The Bluetooth client is the only piece
that can't run; everything else is exercised through the replay path.

## 1. Set up (once)

    python --version                 # 3.11 or newer
    pip install -r requirements.txt  # aiohttp, PyYAML, pygame-ce, pytest

Windows: use PowerShell or CMD, plain `python`. WSL: works the same; for the
gauge window you need WSLg (Windows 11, or Windows 10 with the WSLg update) —
`echo $DISPLAY` should print something. If it doesn't, run the daemon in WSL
and the UI from Windows (WSL2 shares localhost with Windows).

## 2. Run the test suite

    pytest

## 3. Make a capture and run the daemon

    python -m tools.synth --out capture.jsonl              # ~2 min of fake driving
    python -m tools.synth --out capture_wb.jsonl --wideband  # same, with channel 0x0329
    python -m s300d --replay capture.jsonl --loop --settings-host 127.0.0.1

Leave that running. `--speed 3` plays faster; `--settings-host` is needed off
the Pi because config.yaml points the settings page at the hotspot address.

## 4. Open the two front ends

In a second terminal:

    python -m s300ui --windowed        # the 800x480 cluster in a window

Keys: Enter / A / Space = ack alarms, R = release/resume Bluetooth (no-op on
replay), Esc / Q = quit.

In a browser: <http://127.0.0.1:8081> — the phone settings page. Try lowering
`ect_high` warn to 90 and Save: the coolant tile goes amber within a frame and
the banner shows ECT HIGH. Tick `overboost` on, set warn to -5, Save: boost
alarm fires. Blank a scaling override and Save: the replay source bounces and
re-handshakes (watch the daemon log).

The synthetic capture does a warm-up, repeated pulls through the shift light
(amber 7400 / red 7900 / flash 8100), a knock-retard burst at t≈40 s, coolant
creeping to 116 °C (warn → critical, which latches until you ack), and a
battery sag at idle around t≈60 s.

## 5. Watch the raw feed (optional)

    python -m tools.replay capture.jsonl            # decoded frames as JSON lines
    python - <<'EOF'
    import asyncio, aiohttp, json
    async def main():
        async with aiohttp.ClientSession() as s, s.ws_connect("ws://127.0.0.1:8080/ws") as ws:
            for _ in range(5): print(json.loads((await ws.receive()).data))
    asyncio.run(main())
    EOF

## What you cannot test off the Pi

- The RFCOMM link itself (`s300d/client.py` is covered by the fake-socket tests).
- kmsdrm rendering and the PSPi 6 buttons (use `--windowed` + keyboard here).
- `deploy/` scripts — they're Pi-only (systemd, nmcli, raspi-config).
