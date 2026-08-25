# s300-cluster

Hondata S300 V3 binary-protocol library for a Raspberry Pi gauge cluster.
This layer is pure logic — framing, channel decoding, unit scaling. No
transport, no UI. Python 3.11+. Protocol/client layers are standard-library only; the daemon
adds aiohttp and PyYAML (`pip install -r requirements.txt`).

## Layout

    s300d/protocol.py   header pack/unpack, read_exact(), read_packet(), command builders,
                        DeviceInfo / DatalogInfo parsers
    s300d/channels.py   channel ID registry, SizeAndType decode, scaling table,
                        build_offset_table(), decode_packet()
    s300d/config.py     dataclass-backed config.yaml loader (minimal stdlib YAML subset)
    s300d/client.py     LiveSource: RFCOMM socket, state machine, backoff, release()/resume()
    s300d/alarms.py     degF->degC boundary, boost from baro, shift light, AlarmEngine
    s300d/server.py     aiohttp static + /ws on 127.0.0.1, broadcast loop, slow-client eviction
    s300d/__main__.py   daemon entry point: python -m s300d [--replay capture]
    s300d/settings.py   phone settings page + API (hotspot only), atomic save + hot reload
    s300ui/             native pygame cluster (KMS on the Pi, --windowed on a laptop)
    deploy/             install.sh, push.sh, systemd units, hotspot/overlay/pair helpers
    INSTALL.md          step-by-step PSPi 6 deployment
    LOCAL_TESTING.md    running everything on a laptop with a synthetic capture
    tools/synth.py      generate a synthetic capture for bench testing
    tools/record.py     capture raw 0x35 payloads to JSONL (tools/CAPTURE_FORMAT.md)
    tools/replay.py     ReplaySource: same interface as LiveSource, from a capture
    tests/              pytest suite
    config.yaml         mac, rfcomm_channel, poll_hz, scaling_overrides (placeholders)
    docs/PROTOCOL.md    protocol ground truth — the permanent reference

## Running tests

    pip install pytest        # dev dependency only
    pytest

## Usage sketch

    from s300d import protocol, channels

    # after sending protocol.cmd_get_datalog_channel_ids() and reading the reply:
    channel_list = channels.parse_channel_ids(payload_0x31)
    table = channels.build_offset_table(channel_list, cfg.scaling_overrides)  # once per connection

    # per DL_GetDatalogPacket reply:
    values = channels.decode_packet(table, payload_0x35)   # pure, no I/O

`read_packet(recv, buf)` takes any `recv(n) -> bytes` callable plus a persistent
`bytearray` so partial and coalesced packets on the byte stream are handled.

## Calibration

`CT_RPM` and `CT_RETARD` factors are provisional (see TODOs in `channels.py`).
Override any type's scaling in `config.yaml`:

    scaling_overrides:
      CT_RPM: 0.25              # scale only
      CT_RETARD:
        scale: 0.5
        offset: 0

## Live and replay

    python -m tools.record --out capture.bin --seconds 60 --print   # live, prints decoded JSON lines
    python -m tools.replay capture.bin [--speed 2] [--loop]          # same output shape, off-car

Both `LiveSource` and `ReplaySource` expose `frames()`, `state`, `channel_list`,
`release()`, `resume()`, `close()`. Call `release()` to hand the RFCOMM channel to
SManager or the phone; `resume()` reconnects and re-runs the full handshake.

## Running the daemon

    python -m s300d                              # live ECU from config.yaml
    python -m s300d --replay capture.bin --speed 2 --loop   # off-car

Serves `./ui` on http://127.0.0.1:8080 and a WebSocket at `/ws` (loopback only).
Alarms, shift-light stages and server settings all live in `config.yaml`.
UI commands: `{"cmd": "ack_alarms"}`, `{"cmd": "release_bt"}`, `{"cmd": "resume_bt"}`.

## Native cluster UI

    python -m s300ui --windowed       # on a laptop, with the daemon running (e.g. --replay)

On the Pi it is started by `s300ui.service` on tty1 via DRM/KMS. See INSTALL.md.
