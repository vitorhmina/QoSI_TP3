"""Synthetic training flows (client -> server direction).
Each profile generates the first packets of one flow as (times, sizes). They pass through the
same features.vector() as live traffic. The profiles cover the lab traffic (ping probes, iperf3
UDP/TCP) plus common real-world variants (RTP codecs, frame-based video, TCP live upload, small
TCP requests, QUIC-like bulk), so protocol alone is not enough to tell the classes apart.
"""
import math
import random

from features import MAX_WAIT, WINDOW, vector

MSS_PKT = (1500, 1492, 1452)            # full TCP segments (IP size)


def _jit(rng, x, rel):
    return max(1e-5, x * (1 + rng.gauss(0, rel)))


def _series(rng, n, size_fn, iat_fn, start=None):
    if start:
        times, sizes = list(start[0]), list(start[1])
    else:
        times, sizes = [0.0], [size_fn()]
    while len(sizes) < n:
        times.append(times[-1] + iat_fn())
        sizes.append(size_fn())
    return times[:n], sizes[:n]


def _tcp_open(rng):
    """SYN, then the ACK one RTT later (the client waits for the SYN-ACK)."""
    rtt = rng.uniform(0.005, 0.08)
    return [0.0, rtt], [60, 52], rtt


# ---- voip (EF) ----
def voip_rtp(rng, n):
    ptime, size = rng.choice([(0.02, 200), (0.01, 120), (0.02, 60), (0.02, 0), (0.03, 280)])
    var = size == 0                                         # Opus: variable size
    return "udp", *_series(rng, n, lambda: rng.randint(90, 190) if var else size,
                           lambda: _jit(rng, ptime, 0.05))


def voip_icmp(rng, n):
    size = rng.choice([84, 128, 200, 528, 1028, 1228, 1428])  # ping -s payload + 28
    interval = rng.choice([0.01, 0.02, 0.05, 0.2, 1.0])
    return "icmp", *_series(rng, n, lambda: size, lambda: _jit(rng, interval, 0.02))


# ---- video (AF31) ----
def video_udp_cbr(rng, n):
    rate = rng.uniform(0.5, 20) * 1e6
    size = rng.choice([1344, 1428, 1476, 1488, 1500])
    start = None
    if rng.random() < 0.5:                                  # iperf3 UDP hello, then one RTT
        start = ([0.0, rng.uniform(0.005, 0.08)], [32, size])
    return "udp", *_series(rng, n, lambda: size, lambda: _jit(rng, size * 8 / rate, 0.1), start)


def _frames(rng, n, rate, fps, mtu, start=None):
    times, sizes = (list(start[0]), list(start[1])) if start else ([], [])
    t = times[-1] + rng.uniform(0, 1 / fps) if times else 0.0
    while len(sizes) < n:
        frame = max(200, int(rng.gauss(rate / 8 / fps, rate / 8 / fps * 0.3)))
        for k in range(math.ceil(frame / mtu)):
            times.append(t + k * rng.uniform(5e-5, 3e-4))
            sizes.append(min(mtu, frame - k * mtu) + 40)
        t += _jit(rng, 1 / fps, 0.05)
    return times[:n], sizes[:n]


def video_rtp_frames(rng, n):
    return "udp", *_frames(rng, n, rng.uniform(1, 10) * 1e6, rng.choice([24, 25, 30, 60]),
                          rng.choice([800, 1000, 1200, 1400]))


def video_tcp_upload(rng, n):                               # RTMP-like live upload
    t, s, rtt = _tcp_open(rng)
    t += [rtt + 1e-4, 2 * rtt + 1e-4]
    s += [rng.randint(100, 600), rng.randint(100, 600)]     # stream setup messages
    return "tcp", *_frames(rng, n, rng.uniform(1, 8) * 1e6, rng.choice([25, 30]), 1448, (t, s))


# ---- bulk (BE) ----
def bulk_tcp(rng, n):                                       # iperf3 / file upload
    t, s, rtt = _tcp_open(rng)
    if rng.random() < 0.6:
        t.append(rtt + 1e-4); s.append(89)                  # iperf3 cookie
    seg = rng.choice(MSS_PKT)
    gap = seg * 8 / (rng.uniform(5, 100) * 1e6)
    pause = 0.0
    while len(s) < n:                                       # slow start: back-to-back bursts
        for _ in range(rng.randint(2, 10)):
            t.append(t[-1] + pause + _jit(rng, gap, 0.3)); s.append(seg)
            pause = 0.0
        pause = rng.uniform(0, rtt)                         # wait for ACKs
    return "tcp", t[:n], s[:n]


def bulk_tcp_capped(rng, n):                                # iperf3 -b: paced bursts
    t, s, rtt = _tcp_open(rng)
    seg, rate = rng.choice(MSS_PKT), rng.uniform(1, 10) * 1e6
    pause = rtt
    while len(s) < n:
        burst = rng.randint(1, 8)
        for _ in range(burst):
            t.append(t[-1] + pause + rng.uniform(2e-5, 2e-4)); s.append(seg)
            pause = 0.0
        pause = _jit(rng, burst * seg * 8 / rate, 0.2)      # pacing timer
    return "tcp", t[:n], s[:n]


def bulk_tcp_chatter(rng, n):                               # control channels, small requests
    t, s, rtt = _tcp_open(rng)
    return "tcp", *_series(rng, n, lambda: rng.choice([52, 52, 52, rng.randint(60, 700)]),
                           lambda: rng.expovariate(1 / rng.uniform(rtt, 0.5)), (t, s))


def bulk_udp(rng, n):                                       # QUIC-like upload: bursty
    size = rng.randint(1252, 1350)
    gap = size * 8 / (rng.uniform(5, 80) * 1e6)
    return "udp", *_series(rng, n, lambda: size if rng.random() > 0.1 else rng.randint(60, 120),
                           lambda: rng.uniform(1e-5, 2e-4) if rng.random() < 0.7 else
                           rng.uniform(gap, 30 * gap))


PROFILES = {   # label -> [(generator, weight)]
    "voip": [(voip_rtp, 2), (voip_icmp, 1)],
    "video": [(video_udp_cbr, 2), (video_rtp_frames, 1), (video_tcp_upload, 1)],
    "bulk": [(bulk_tcp, 2), (bulk_tcp_capped, 1), (bulk_tcp_chatter, 1), (bulk_udp, 1)],
}


def cut(times, window=WINDOW, max_wait=MAX_WAIT):
    """Packets the VNF has when it decides: the window, or fewer for a slow flow (same rule
    as features.Flow.ready), so training sees exactly what the live classifier sees."""
    for i in range(1, len(times)):
        if times[i] - times[0] >= max_wait:
            return i + 1
    return window


def generate(count, seed=1, window=WINDOW):
    """count flows per class -> list of (label, profile, feature vector)."""
    rng = random.Random(seed)
    rows = []
    for label, gens in PROFILES.items():
        fns, weights = zip(*gens)
        for _ in range(count):
            gen = rng.choices(fns, weights)[0]
            proto, times, sizes = gen(rng, window)
            n = cut(times, window)
            times, sizes = times[:n], sizes[:n]
            rows.append((label, gen.__name__, vector(proto, times, sizes)))
    return rows
