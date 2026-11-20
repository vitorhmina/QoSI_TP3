#!/usr/bin/env python3
"""Scenario runner: the same load through different paths/VNF sets, to measure what each adds.
  bypass  direct r1-r2 link, no VNFs           off    chain, all VNFs off (cost of the extra hops)
  nopol   chain, fw + cls on, policer off       on     chain, all VNFs on
Load: VoIP (G.711-like: 200 B every 20 ms), 8 Mbit/s UDP video (HD stream), 4 TCP bulk streams
starting 5 s later, which saturates the 20 Mbit/s bottleneck.
Usage: sudo python3 tests/run_tests.py [--name NAME] [--runs N] [--scenarios bypass off nopol on]
Results: results/<name>/<scenario>/run<k>/ plus summary.csv and graphs in results/<name>/
"""
import argparse, csv, json, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "orchestrator"))
import qosi
PORT = "5301"
DUR_PING, DUR_VIDEO, DUR_BULK, BULK_DELAY, VIDEO_BW = 35, 30, 20, 5, "8M"
# VoIP emulated as a G.711 call with 20 ms packetisation: 200-byte IP packets (172 B ping payload
# + 28 B of headers), 50 per second, ~80 kbit/s
VOIP_SIZE, VOIP_INTERVAL = "172", "0.02"
VNFS = ("fw", "cls", "pol")
SCENARIOS = {   # name -> (path, VNFs turned on; the others are off)
    "bypass": ("bypass", ()),
    "off":    ("chain", ()),
    "nopol":  ("chain", ("fw", "cls")),
    "on":     ("chain", VNFS),
}
NAN = float("nan")


def vcmd(node):
    return ["vcmd", "-c", f"{qosi.SESSION}/{node}", "--"]


def run(node, *cmd):
    return subprocess.run(vcmd(node) + list(cmd), capture_output=True, text=True)


def start(node, outfile, *cmd):
    return subprocess.Popen(vcmd(node) + list(cmd), stdout=open(outfile, "w"), stderr=subprocess.DEVNULL)


def vnf(name, action):
    subprocess.run(["docker", "exec", f"vnf-{name}", "python3", "/app/vnf.py", action], capture_output=True)


def run_once(scn, out):
    mode, active = SCENARIOS[scn]
    os.makedirs(out, exist_ok=True)
    qosi.set_mode(mode)
    for v in VNFS:
        vnf(v, "on" if v in active else "off")
    time.sleep(2)
    r = run("atk", "ping", "-c", "3", "-W", "1", "10.0.20.10")      # firewall check
    open(f"{out}/atk.txt", "w").write(r.stdout + f"\nRESULT: {'blocked' if r.returncode else 'passed'}\n")
    print("  atk ->", "blocked" if r.returncode else "passed")
    vnf("mon", "on")                                                  # resets mon.csv
    time.sleep(3)
    procs = [start("c-voip", f"{out}/ping.txt", "ping", "-s", VOIP_SIZE, "-i", VOIP_INTERVAL,
                   "-w", str(DUR_PING), "-D", "10.0.20.10"),
             start("c-video", f"{out}/video.json", "iperf3", "-c", "10.0.20.20", "-p", PORT,
                   "-u", "-b", VIDEO_BW, "-t", str(DUR_VIDEO), "-J")]
    time.sleep(BULK_DELAY)
    procs.append(start("c-bulk", f"{out}/bulk.json", "iperf3", "-c", "10.0.20.30", "-p", PORT,
                       "-t", str(DUR_BULK), "-P", "4", "-J"))
    for p in procs:
        p.wait()
    time.sleep(2)
    subprocess.run(["docker", "cp", "vnf-mon:/var/log/mon.csv", f"{out}/mon.csv"], capture_output=True)


def pct(v, p):
    s = sorted(v)
    return s[min(len(s) - 1, int(p * len(s)))] if s else NAN


def summarize(d):
    txt = open(f"{d}/ping.txt").read()
    pts = [(float(m[1]), float(m[2])) for m in re.finditer(r"\[(\d+\.\d+)\].*time=([\d.]+) ms", txt)]
    t, rtt = [p[0] for p in pts], [p[1] for p in pts]
    loss = re.search(r"([\d.]+)% packet loss", txt)
    s = {"rtt_avg": sum(rtt) / len(rtt) if rtt else NAN, "rtt_p95": pct(rtt, .95), "rtt_max": max(rtt, default=NAN),
         "jitter": sum(abs(a - b) for a, b in zip(rtt, rtt[1:])) / max(1, len(rtt) - 1),
         "voip_loss": float(loss[1]) if loss else NAN,
         "video_mbps": NAN, "video_jitter": NAN, "video_loss": NAN, "bulk_mbps": NAN, "bulk_retr": NAN}
    try:
        v = json.load(open(f"{d}/video.json"))["end"]["sum"]
        s.update(video_mbps=v["bits_per_second"] / 1e6, video_jitter=v["jitter_ms"], video_loss=v["lost_percent"])
    except Exception:
        print(f"  WARNING: could not parse {d}/video.json")
    try:
        b = json.load(open(f"{d}/bulk.json"))["end"]
        s.update(bulk_mbps=b["sum_received"]["bits_per_second"] / 1e6, bulk_retr=b["sum_sent"]["retransmits"])
    except Exception:
        print(f"  WARNING: could not parse {d}/bulk.json")
    s["atk_blocked"] = "RESULT: blocked" in open(f"{d}/atk.txt").read()
    return s, t, rtt


def report(res, base):
    keys = ["rtt_avg", "rtt_p95", "rtt_max", "jitter", "voip_loss", "video_mbps", "video_jitter",
            "video_loss", "bulk_mbps", "bulk_retr"]
    mean = {scn: {k: sum(r[0][k] for r in runs) / len(runs) for k in keys} for scn, runs in res.items()}
    for scn, runs in res.items():
        mean[scn]["atk_blocked"] = all(r[0]["atk_blocked"] for r in runs)
    names = list(res)
    print("\n" + " " * 28 + "".join(f"{n:>10}" for n in names))
    for k in ["atk_blocked"] + keys:
        print(f"{k:28}" + "".join(f"{mean[n][k]:>10.2f}" if k != "atk_blocked"
                                    else f"{'yes' if mean[n][k] else 'no':>10}" for n in names))
    with open(f"{base}/summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric"] + names)
        for k in ["atk_blocked"] + keys:
            w.writerow([k] + [mean[n][k] for n in names])
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib missing: skipping graphs"); return
    fig, ax = plt.subplots(figsize=(9, 4))
    for scn, runs in res.items():
        _, t, rtt = runs[0]
        if t:
            ax.plot([x - t[0] for x in t], rtt, label=scn)
    ax.set(xlabel="time (s)", ylabel="VoIP RTT (ms, log)", yscale="log", title="VoIP latency under load")
    ax.legend(); fig.tight_layout(); fig.savefig(f"{base}/voip_latency.png", dpi=130)
    fig, axs = plt.subplots(1, 4, figsize=(14, 3.5))
    for ax, (k, title) in zip(axs, [("rtt_avg", "VoIP RTT avg (ms)"), ("jitter", "VoIP jitter (ms)"),
                                    ("bulk_mbps", "Bulk (Mbit/s)"), ("video_loss", "Video loss (%)")]):
        ax.bar(names, [mean[n][k] for n in names]); ax.set_title(title)
    fig.tight_layout(); fig.savefig(f"{base}/comparison.png", dpi=130)
    try:
        rows = list(csv.DictReader(open(f"{base}/on/run1/mon.csv")))
        x = [int(r["time"]) - int(rows[0]["time"]) for r in rows]
        fig, ax = plt.subplots(figsize=(9, 4))
        ax.stackplot(x, *[[float(r[f"{c}_mbps"]) for r in rows] for c in ("voip", "video", "bulk")],
                     labels=["voip", "video", "bulk"])
        ax.set(xlabel="time (s)", ylabel="Mbit/s", title="Throughput per class after the chain (VNFs on)")
        ax.legend(); fig.tight_layout(); fig.savefig(f"{base}/mon_on.png", dpi=130)
    except Exception:
        pass
    print(f"\nSaved summary.csv and graphs in {base}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=time.strftime("%Y%m%d_%H%M%S"))
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--scenarios", nargs="+", default=list(SCENARIOS), choices=list(SCENARIOS))
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("Run with sudo.")
    qosi.SESSION = qosi.find_session()
    base = f"{ROOT}/results/{a.name}"
    os.makedirs(base, exist_ok=True)
    for n in ("srv-video", "srv-bulk"):
        run(n, "pkill", "iperf3")
    servers = [subprocess.Popen(vcmd(n) + ["iperf3", "-s", "-p", PORT],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
               for n in ("srv-video", "srv-bulk")]
    time.sleep(1)
    res = {}
    try:
        for k in range(1, a.runs + 1):
            for scn in a.scenarios:
                print(f"\n=== run {k}/{a.runs}, scenario {scn} ===")
                d = f"{base}/{scn}/run{k}"
                run_once(scn, d)
                res.setdefault(scn, []).append(summarize(d))
    finally:
        for n in ("srv-video", "srv-bulk"):
            run(n, "pkill", "iperf3")
        for s in servers:
            s.terminate()
        qosi.set_mode("chain")                      # leave the topology with everything on
        for v in ("fw", "cls", "pol"):
            vnf(v, "on")
    if res:
        report(res, base)
    if os.environ.get("SUDO_USER"):
        subprocess.run(["chown", "-R", os.environ["SUDO_USER"], f"{ROOT}/results"])


if __name__ == "__main__":
    main()
