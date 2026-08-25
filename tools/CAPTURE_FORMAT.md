# Capture file format (JSONL)

One JSON object per line, UTF-8, `\n` terminated.

Line 1 — channel header, written once after the handshake:

    {"type": "channels", "device": "S300",
     "channels": [[256, 131], [257, 132], ...],     # [channel_id, size_and_type] in 0x31 order
     "packet_size": 8}

Every following line — one raw 0x35 payload (header bytes stripped):

    {"type": "packet", "t": 12345.678901, "raw": "20120000012301 55"}

`t` is `time.monotonic()` on the recording machine at receive time; only
differences between values are meaningful. `raw` is the payload as lowercase
hex with no separators.

If the recorder reconnects and the handshake yields a different channel list,
a new `channels` line is written and later packets use the new layout.
