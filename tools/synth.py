"""Generate a synthetic capture for bench testing (no car needed).

    python -m tools.synth --out capture.jsonl [--seconds 90] [--wideband]

Simulates a warm-up, a few pulls through the shift light, a knock event, a
coolant creep past the warn threshold, and a battery sag at idle.
"""
import argparse
import json
import math
import struct

CHANNELS = [  # (id, size_and_type) — see docs/PROTOCOL.md
    (0x0100, 0x83), (0x0101, 0x84), (0x0102, 0x42), (0x0110, 0x85), (0x0120, 0x47),
    (0x0150, 0x50), (0x0160, 0x50), (0x0170, 0x85), (0x0180, 0x59), (0x0200, 0x41),
    (0x0410, 0x4B), (0x0420, 0x42), (0x0712, 0x41),
]
WIDEBAND = (0x0329, 0x9E)


def sim(t):
    """Engine state at time t (s) -> engineering values."""
    cycle = t % 30.0
    if cycle < 8:                       # idle
        rpm, tps = 900 + 40 * math.sin(t * 3), 2.0
    elif cycle < 20:                    # pull to redline
        f = (cycle - 8) / 12.0
        rpm, tps = 1500 + 6800 * f ** 0.8, 100.0
    else:                               # coast down
        rpm, tps = 8300 - 6800 * (cycle - 20) / 10.0, 5.0
    ect_c = min(84 + t * 0.35, 116) if t < 95 else 116 - (t - 95) * 0.2
    iat_c = 28 + 8 * (tps / 100)
    map_kpa = 30 + 70 * (tps / 100)
    vbat = 12.4 if (cycle < 8 and 60 < t < 75) else 14.1
    knock = 3.0 if 40 < t < 45 else 0.0
    knock_count = int(t // 40) * 2
    lam = 0.86 if tps > 80 else 1.0
    return dict(rpm=rpm, speed=rpm / 60, gear=1 if rpm < 4000 else 2, map_kpa=map_kpa, tps=tps,
                iat_f=iat_c * 9 / 5 + 32, ect_f=ect_c * 9 / 5 + 32, baro=101.3, vbat=vbat,
                vtec=rpm > 5500, knock=knock, knock_count=knock_count, boost_cut=False, lam=lam)


def pack(v, wideband):
    raw = struct.pack("<HHBHBBBHBBBBB",
                      int(v["rpm"] / 0.25), int(v["speed"] / 0.01), v["gear"],
                      int(v["map_kpa"] * 10), int((v["tps"] + 10) * 2),
                      int(v["iat_f"]), int(v["ect_f"]), int(v["baro"] * 10),
                      int((v["vbat"] - 6) * 20), int(v["vtec"]), int(v["knock"] * 2),
                      v["knock_count"] & 0xFF, int(v["boost_cut"]))
    if wideband:
        raw += struct.pack("<H", int(v["lam"] * 32768))
    return raw


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="capture.jsonl")
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--hz", type=float, default=10)
    ap.add_argument("--wideband", action="store_true", help="include channel 0x0329")
    a = ap.parse_args(argv)
    chans = CHANNELS + ([WIDEBAND] if a.wideband else [])
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "channels", "device": "S300",
                             "channels": [list(c) for c in chans],
                             "packet_size": len(pack(sim(0), a.wideband))}) + "\n")
        n = int(a.seconds * a.hz)
        for i in range(n):
            t = i / a.hz
            fh.write(json.dumps({"type": "packet", "t": 1000.0 + t,
                                 "raw": pack(sim(t), a.wideband).hex()}) + "\n")
    print("wrote %d packets to %s" % (n, a.out))


if __name__ == "__main__":
    main()
