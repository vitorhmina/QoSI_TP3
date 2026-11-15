#!/usr/bin/env python3
"""Random traffic simulator for the QoSI chain.
Usage: sudo python3 traffic.py [--duration 120] [--seed 1] [--atk]
Same seed = same schedule, so runs can be compared across scenarios.
Only sends traffic the firewall whitelist allows (plus optional atk probes).
"""
import argparse, csv, glob, json, os, random, re, subprocess, sys, threading, time

PORT = "5301"
SRV_VOIP, SRV_VIDEO, SRV_BULK = "10.0.20.10", "10.0.20.20", "10.0.20.30"
LOCK = threading.Lock()
EVENTS = []
T0 = time.time()


def find_session():
    for d in sorted(glob.glob("/tmp/pycore.*"), key=os.path.getmtime, reverse=True):
        if os.path.exists(f"{d}/r1.pid"):
            return d
    sys.exit("No active CORE session: open topology/chain.xml and press play.")


def node(name, *cmd, timeout=None):
    return subprocess.run(["vcmd", "-c", f"{SESSION}/{name}", "--", *cmd],
                          capture_output=True, text=True, timeout=timeout)


def record(kind, params, mbps="", extra="", jitter=""):
    ev = {"t": round(time.time() - T0, 1), "class": kind, "params": params,
          "mbps": mbps, "loss_or_retr": extra, "jitter_ms": jitter}
    with LOCK:
        EVENTS.append(ev)
        print(f"[{ev['t']:6.1f}s] {kind:6} {params:28} -> "
              f"{mbps} Mbit/s {extra} {jitter}".rstrip())


def idle_then_plan(rng, deadline, lo, hi, dmin, dmax):
    """Sleep a random idle time, then return a random duration (or None if out of time)."""
    time.sleep(min(rng.uniform(lo, hi), max(0, deadline - time.time())))
    left = int(deadline - time.time())
    if left < 4:
        return None
    return min(rng.randint(dmin, dmax), left)


def video_worker(rng, deadline):
    while True:
        dur = idle_then_plan(rng, deadline, 2, 8, 5, 15)
        if dur is None:
            return
        rate = rng.choice([2, 4, 8, 12])
        params = f"UDP {rate}M for {dur}s"
        try:
            r = node("c-video", "iperf3", "-c", SRV_VIDEO, "-p", PORT, "-u",
                     "-b", f"{rate}M", "-t", str(dur), "-J", timeout=dur + 20)
            s = json.loads(r.stdout)["end"]["sum"]
            record("video", params, round(s["bits_per_second"] / 1e6, 2),
                   f"loss {s['lost_percent']:.2f}%", f"jitter {s['jitter_ms']:.2f}")
        except Exception as e:
            record("video", params, extra=f"FAILED ({type(e).__name__})")


def bulk_worker(rng, deadline):
    while True:
        dur = idle_then_plan(rng, deadline, 3, 10, 5, 20)
        if dur is None:
            return
        streams = rng.randint(1, 4)
        params = f"TCP x{streams} for {dur}s"
        try:
            r = node("c-bulk", "iperf3", "-c", SRV_BULK, "-p", PORT, "-t", str(dur),
                     "-P", str(streams), "-J", timeout=dur + 20)
            e = json.loads(r.stdout)["end"]
            record("bulk", params, round(e["sum_received"]["bits_per_second"] / 1e6, 2),
                   f"retr {e['sum_sent'].get('retransmits', 0)}")
        except Exception as e:
            record("bulk", params, extra=f"FAILED ({type(e).__name__})")


def atk_worker(rng, deadline):
    while True:
        dur = idle_then_plan(rng, deadline, 8, 20, 4, 4)
        if dur is None:
            return
        r = node("atk", "ping", "-c", "3", "-W", "1", SRV_VOIP)
        record("atk", "3 pings (should be blocked)",
               extra="BLOCKED" if r.returncode != 0 else "PASSED (firewall off?)")


def main():
    global SESSION, T0
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=120)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--atk", action="store_true", help="also send blocked probes from atk")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("Run with sudo.")
    SESSION = find_session()
    out = a.out or f"results/traffic_seed{a.seed}"
    os.makedirs(out, exist_ok=True)

    for n in ("srv-video", "srv-bulk"):          # clean start, then servers
        node(n, "pkill", "iperf3")
    servers = [subprocess.Popen(["vcmd", "-c", f"{SESSION}/{n}", "--", "iperf3", "-s", "-p", PORT],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
               for n in ("srv-video", "srv-bulk")]
    time.sleep(1)

    print(f"Random traffic: {a.duration}s, seed {a.seed} (Ctrl+C to stop)")
    T0 = time.time()
    deadline = T0 + a.duration
    workers = [threading.Thread(target=video_worker, args=(random.Random(a.seed * 10 + 1), deadline)),
               threading.Thread(target=bulk_worker, args=(random.Random(a.seed * 10 + 2), deadline))]
    if a.atk:
        workers.append(threading.Thread(target=atk_worker, args=(random.Random(a.seed * 10 + 3), deadline)))
    for w in workers:
        w.daemon = True
        w.start()

    # VoIP runs the whole time: ping every 0.2 s
    try:
        ping = subprocess.run(["vcmd", "-c", f"{SESSION}/c-voip", "--", "ping", "-i", "0.2",
                               "-w", str(a.duration), "-D", SRV_VOIP],
                              capture_output=True, text=True, timeout=a.duration + 15)
        with open(f"{out}/ping.txt", "w") as f:
            f.write(ping.stdout)
        m = re.search(r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", ping.stdout)
        loss = re.search(r"([\d.]+)% packet loss", ping.stdout)
        if m:
            record("voip", "ping 0.2s whole run", extra=f"RTT avg {m[2]} max {m[3]} ms, "
                   f"loss {loss[1] if loss else '?'}%", jitter=f"mdev {m[4]}")
        for w in workers:
            w.join(timeout=25)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        for s in servers:
            s.terminate()
        for n in ("srv-video", "srv-bulk"):
            node(n, "pkill", "iperf3")

    with open(f"{out}/events.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t", "class", "params", "mbps", "loss_or_retr", "jitter_ms"])
        w.writeheader()
        w.writerows(EVENTS)
    print(f"\nSaved {out}/events.csv and {out}/ping.txt")


if __name__ == "__main__":
    main()
