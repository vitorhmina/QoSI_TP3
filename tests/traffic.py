#!/usr/bin/env python3
"""Random traffic simulator for the QoSI chain.
Usage: sudo python3 tests/traffic.py [--duration 0] [--seed N] [--atk]
  --duration 0   run until Ctrl+C (default); Ctrl+C finishes running streams, twice = force
  --seed N       reproduce a schedule (a random seed is chosen and printed otherwise)
  --voip-size B / --voip-interval S   VoIP ping payload and spacing (default 172 B every 20 ms:
                                      200-byte packets, 50/s, like a G.711 call)
Load changes through random phases (calm / normal / busy / storm).
Only sends traffic the firewall whitelist allows (plus optional atk probes).
"""
import argparse, csv, glob, json, os, random, re, subprocess, sys, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = "5301"
SRV_VOIP, SRV_VIDEO, SRV_BULK = "10.0.20.10", "10.0.20.20", "10.0.20.30"
LOCK = threading.Lock()
EVENTS = []
T0 = time.time()
STOP = threading.Event()
PHASE = {"name": "normal"}
SESSION = None

# gap = idle seconds between streams, vdur/bdur = stream lengths, cap = bulk rate limit
PHASES = {
    "calm":   dict(w=2, gap=(6, 20), video=[1, 2, 4],   vdur=(5, 12),  streams=[1],       cap=["2M", "4M"], bdur=(5, 15)),
    "normal": dict(w=4, gap=(2, 8),  video=[2, 4, 8],   vdur=(6, 15),  streams=[1, 2, 3], cap=[None, "8M"],  bdur=(8, 20)),
    "busy":   dict(w=3, gap=(0, 3),  video=[8, 12, 16], vdur=(8, 20),  streams=[2, 4],    cap=[None],       bdur=(10, 30)),
    "storm":  dict(w=1, gap=(0, 1),  video=[12, 16],    vdur=(10, 25), streams=[4, 6, 8], cap=[None],       bdur=(15, 40)),
}


def find_session():
    for d in sorted(glob.glob("/tmp/pycore.*"), key=os.path.getmtime, reverse=True):
        if os.path.exists(f"{d}/r1.pid"):
            return d
    sys.exit("No active CORE session: open topology/chain.xml and press play.")


def node(name, *cmd, timeout=None):
    # own session: a Ctrl+C in the terminal does not kill running streams
    return subprocess.run(["vcmd", "-c", f"{SESSION}/{name}", "--", *cmd], capture_output=True,
                          text=True, timeout=timeout, start_new_session=True)


def log(msg):
    with LOCK:
        print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)


def record(kind, params, mbps="", extra="", verb="ENDED"):
    with LOCK:
        EVENTS.append({"t": round(time.time() - T0, 1), "class": kind, "params": params,
                       "mbps": mbps, "detail": extra})
    if mbps != "":
        log(f"{verb:8} {kind:5} {mbps} Mbit/s {extra}".rstrip())
    else:
        log(f"{verb:8} {kind:5} {extra}".rstrip())


def wait_gap(rng, deadline):
    """Idle for a random time that depends on the phase. False = time to stop."""
    lo, hi = PHASES[PHASE["name"]]["gap"]
    STOP.wait(min(rng.uniform(lo, hi), max(0, deadline - time.time())))
    return not STOP.is_set() and deadline - time.time() > 3


def fit(dur, deadline):
    return int(min(dur, deadline - time.time()))


def phase_worker(rng, deadline):
    names = list(PHASES)
    weights = [PHASES[n]["w"] for n in names]
    while not STOP.is_set() and deadline - time.time() > 1:
        PHASE["name"] = rng.choices(names, weights)[0]
        length = rng.randint(15, 45)
        log(f"PHASE    {PHASE['name']} (~{length}s)")
        with LOCK:
            EVENTS.append({"t": round(time.time() - T0, 1), "class": "phase",
                           "params": PHASE["name"], "mbps": "", "detail": ""})
        STOP.wait(length)


def video_worker(rng, deadline):
    while wait_gap(rng, deadline):
        p = PHASES[PHASE["name"]]
        dur = fit(rng.randint(*p["vdur"]), deadline)
        rate = round(rng.choice(p["video"]) * rng.uniform(0.7, 1.3), 1)
        params = f"UDP {rate}M for {dur}s"
        log(f"STARTING video  UDP {rate} Mbit/s for {dur}s")
        try:
            r = node("c-video", "iperf3", "-c", SRV_VIDEO, "-p", PORT, "-u", "-b", f"{rate}M",
                     "-t", str(dur), "-J", timeout=dur + 20)
            s = json.loads(r.stdout)["end"]["sum"]
            record("video", params, round(s["bits_per_second"] / 1e6, 2),
                   f"loss {s['lost_percent']:.2f}% jitter {s['jitter_ms']:.2f}ms")
        except Exception as e:
            record("video", params, extra=f"FAILED ({type(e).__name__})")


def bulk_worker(rng, deadline):
    while wait_gap(rng, deadline):
        p = PHASES[PHASE["name"]]
        dur = fit(rng.randint(*p["bdur"]), deadline)
        streams, cap = rng.choice(p["streams"]), rng.choice(p["cap"])
        params = f"TCP x{streams} cap {cap or 'none'} for {dur}s"
        log(f"STARTING bulk   TCP x{streams}, cap {cap or 'none'}, for {dur}s")
        cmd = ["iperf3", "-c", SRV_BULK, "-p", PORT, "-t", str(dur), "-P", str(streams), "-J"]
        if cap:
            cmd += ["-b", cap]
        try:
            r = node("c-bulk", *cmd, timeout=dur + 20)
            e = json.loads(r.stdout)["end"]
            record("bulk", params, round(e["sum_received"]["bits_per_second"] / 1e6, 2),
                   f"retr {e['sum_sent'].get('retransmits', 0)}")
        except Exception as e:
            record("bulk", params, extra=f"FAILED ({type(e).__name__})")


def atk_worker(rng, deadline):
    while True:
        STOP.wait(min(rng.uniform(6, 25), max(0, deadline - time.time())))
        if STOP.is_set() or deadline - time.time() < 3:
            return
        n, target = rng.randint(2, 5), rng.choice([SRV_VOIP, SRV_VIDEO, SRV_BULK])
        log(f"STARTING atk    {n} probe pings to {target} (should be blocked)")
        r = node("atk", "ping", "-c", str(n), "-W", "1", target)
        record("atk", f"{n} pings to {target}",
               extra="BLOCKED" if r.returncode != 0 else "PASSED (firewall off?)")


def voip_worker(deadline, out, size, interval):
    mbps = (size + 28) * 8 / interval / 1e6
    log(f"STARTING voip   {size}B every {interval * 1000:.0f}ms (~{mbps:.2f} Mbit/s), continuous")
    open(f"{out}/ping.txt", "w").close()
    while not STOP.is_set() and deadline - time.time() > 2:
        w = int(min(20, deadline - time.time()))
        try:
            r = node("c-voip", "ping", "-i", str(interval), "-s", str(size), "-w", str(w),
                     "-D", SRV_VOIP, timeout=w + 15)
        except Exception as e:
            record("voip", "", extra=f"FAILED ({type(e).__name__})")
            STOP.wait(2)
            continue
        with open(f"{out}/ping.txt", "a") as f:
            f.write(r.stdout)
        m = re.search(r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", r.stdout)
        loss = re.search(r"([\d.]+)% packet loss", r.stdout)
        if m:
            record("voip", "", extra=f"RTT avg {m[2]} max {m[3]} ms, loss {loss[1] if loss else '?'}%",
                   verb="REPORT")


def main():
    global SESSION, T0
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=0, help="seconds, 0 = until Ctrl+C")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--atk", action="store_true", help="also send blocked probes from atk")
    ap.add_argument("--voip-size", type=int, default=172, help="ping payload bytes (+28 B headers)")
    ap.add_argument("--voip-interval", type=float, default=0.02, help="seconds between VoIP packets")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("Run with sudo.")
    SESSION = find_session()
    seed = a.seed if a.seed is not None else random.randrange(1, 10**6)
    out = a.out or f"{ROOT}/results/traffic_{time.strftime('%Y%m%d_%H%M%S')}_seed{seed}"
    os.makedirs(out, exist_ok=True)
    deadline = float("inf") if a.duration == 0 else time.time() + a.duration

    for n in ("srv-video", "srv-bulk"):          # clean start, then servers
        node(n, "pkill", "iperf3")
    servers = [subprocess.Popen(["vcmd", "-c", f"{SESSION}/{n}", "--", "iperf3", "-s", "-p", PORT],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
               for n in ("srv-video", "srv-bulk")]
    time.sleep(1)

    T0 = time.time()
    print(f"Random traffic: {'until Ctrl+C' if a.duration == 0 else f'{a.duration}s'}, seed {seed}")
    log("run started")
    R = lambda k: random.Random(seed * 10 + k)
    workers = [threading.Thread(target=phase_worker, args=(R(0), deadline)),
               threading.Thread(target=video_worker, args=(R(1), deadline)),
               threading.Thread(target=bulk_worker, args=(R(2), deadline)),
               threading.Thread(target=voip_worker, args=(deadline, out, a.voip_size, a.voip_interval))]
    if a.atk:
        workers.append(threading.Thread(target=atk_worker, args=(R(3), deadline)))
    for w in workers:
        w.daemon = True
        w.start()

    try:
        while time.time() < deadline and not STOP.is_set():
            STOP.wait(0.5)
    except KeyboardInterrupt:
        pass
    STOP.set()
    if a.duration == 0 or time.time() < deadline:
        log("stopping: waiting for running streams to finish (Ctrl+C again to force)")
    forced = False
    try:
        for w in workers:
            w.join()
    except KeyboardInterrupt:
        forced = True
        log("forced stop")
    finally:
        for n in ("srv-video", "srv-bulk"):
            node(n, "pkill", "iperf3")
        if forced:
            for n, p in (("c-video", "iperf3"), ("c-bulk", "iperf3"), ("c-voip", "ping"), ("atk", "ping")):
                node(n, "pkill", p)
        for s in servers:
            s.terminate()

    with open(f"{out}/events.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t", "class", "params", "mbps", "detail"])
        w.writeheader()
        w.writerows(EVENTS)
    log("run finished")
    print(f"\nSaved {out}/events.csv and {out}/ping.txt")


if __name__ == "__main__":
    main()
    