"""Flow features for the ML classifier.
A flow is described by its first packets in the client -> server direction. IPs and ports are
left out on purpose: the model has to recognise the traffic by its behaviour (protocol, packet
sizes, timing), which also works when ports are random or the payload is encrypted.
"""
import math

WINDOW = 8          # packets observed before the model decides
MAX_WAIT = 1.0      # seconds: slow flows are decided earlier, with at least 2 packets
SMALL, LARGE = 200, 1000  # IP sizes (bytes) for the small/large packet fractions

FEATURES = ["tcp", "udp", "icmp",
            "size_mean", "size_std", "size_min", "size_max", "frac_small", "frac_large",
            "iat_mean_ms", "iat_std_ms", "iat_max_ms", "kbps"]


class Flow:
    """Packets seen so far for one undecided flow."""
    __slots__ = ("proto", "times", "sizes")

    def __init__(self, proto):
        self.proto, self.times, self.sizes = proto, [], []

    def add(self, t, size):
        self.times.append(t)
        self.sizes.append(size)

    def ready(self, window=WINDOW, max_wait=MAX_WAIT):
        n = len(self.sizes)
        return n >= window or (n >= 2 and self.times[-1] - self.times[0] >= max_wait)

    def vector(self):
        return vector(self.proto, self.times, self.sizes)


def _mean_std(xs):
    m = sum(xs) / len(xs)
    return m, math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def vector(proto, times, sizes):
    """proto: 'tcp' | 'udp' | 'icmp' | other, times in seconds, sizes = IP total length."""
    n = len(sizes)
    s_mean, s_std = _mean_std(sizes)
    iats = [(b - a) * 1000 for a, b in zip(times, times[1:])] or [0.0]
    i_mean, i_std = _mean_std(iats)
    dur = times[-1] - times[0]
    kbps = sum(sizes[1:]) * 8 / dur / 1000 if dur > 0 else 0.0
    return [float(proto == "tcp"), float(proto == "udp"), float(proto == "icmp"),
            round(s_mean, 1), round(s_std, 1), float(min(sizes)), float(max(sizes)),
            round(sum(s < SMALL for s in sizes) / n, 3), round(sum(s > LARGE for s in sizes) / n, 3),
            round(i_mean, 3), round(i_std, 3), round(max(iats), 3), round(kbps, 1)]
