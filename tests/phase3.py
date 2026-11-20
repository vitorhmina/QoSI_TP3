#!/usr/bin/env python3
"""Parametric chain experiments. Every option accepts several values (cross product).
Usage (sudo):
  python3 tests/phase3.py --name load  --video 4M 8M 12M 16M
  python3 tests/phase3.py --name loss  --loss 0% 1% 3% 5%
  python3 tests/phase3.py --name wan   --delay 0ms 40ms --jitter 0ms 15ms --loss 0% 2% --scenarios off on
  python3 tests/phase3.py --name tune  --scenarios on --bulk-ceil 10mbit 14mbit 19mbit
  python3 tests/phase3.py --name cls   --scenarios on --cls-mode rules ml
Conditions are netem on the edge nodes (both directions), so a delay of 40ms adds ~80ms of RTT.
Nothing is asserted: each combination just records numbers for you to compare.
"""
import argparse, csv, itertools, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "orchestrator"))
import qosi
import run_tests as rt

EDGE = ["c-voip", "c-video", "c-bulk", "atk", "srv-voip", "srv-video", "srv-bulk"]
KEYS = ["rtt_avg", "rtt_p95", "rtt_max", "jitter", "voip_loss", "video_mbps",
        "video_jitter", "video_loss", "bulk_mbps", "bulk_retr"]
COND = ["delay", "netem_jitter", "loss", "video", "bulk_streams", "bulk_ceil", "cls_mode", "voip_interval"]
NO_GRAPH = ("cls_mode", "voip_interval")


def netem(delay, jitter, loss):
    for n in EDGE:
        rt.run(n, "tc", "qdisc", "del", "dev", "eth0", "root")
    spec = []
    if delay != "0ms" or jitter != "0ms":
        spec += ["delay", delay] + ([jitter] if jitter != "0ms" else [])
    if loss != "0%":
        spec += ["loss", loss]
    if not spec:
        return
    for n in EDGE:
        r = rt.run(n, "tc", "qdisc", "add", "dev", "eth0", "root", "netem", *spec)
        if r.returncode:
            sys.exit(f"netem failed on {n}: {r.stderr.strip()}")


def set_vnfs(vnfs_on, c):
    for v in ("fw", "cls", "pol"):
        if not vnfs_on:
            rt.vnf(v, "off")
        elif v == "cls":
            subprocess.run(["docker", "exec", "vnf-cls", "python3", "/app/vnf.py", "on", c["cls_mode"]],
                           capture_output=True)
        elif v == "pol" and c["bulk_ceil"] != "default":
            subprocess.run(["docker", "exec", "-e", f"BULK_CEIL={c['bulk_ceil']}", "vnf-pol",
                            "python3", "/app/vnf.py", "on"], capture_output=True)
        else:
            rt.vnf(v, "on")


def one_run(scn, out, c):
    mode, vnfs_on = rt.SCENARIOS[scn]
    os.makedirs(out, exist_ok=True)
    qosi.set_mode(mode)
    set_vnfs(vnfs_on, c)
    time.sleep(2)
    with open(f"{out}/atk.txt", "w") as f:           # the firewall is not under test here
        f.write("RESULT: n/a\n")
    rt.vnf("mon", "on")                               # resets mon.csv
    time.sleep(3)
    procs = [rt.start("c-voip", f"{out}/ping.txt", "ping", "-i", c["voip_interval"],
                      "-w", str(rt.DUR_PING), "-D", "10.0.20.10"),
             rt.start("c-video", f"{out}/video.json", "iperf3", "-c", "10.0.20.20", "-p", rt.PORT,
                      "-u", "-b", c["video"], "-t", str(rt.DUR_VIDEO), "-J")]
    time.sleep(rt.BULK_DELAY)
    procs.append(rt.start("c-bulk", f"{out}/bulk.json", "iperf3", "-c", "10.0.20.30", "-p", rt.PORT,
                          "-t", str(rt.DUR_BULK), "-P", str(c["bulk_streams"]), "-J"))
    for p in procs:
        p.wait()
    time.sleep(2)
    subprocess.run(["docker", "cp", "vnf-mon:/var/log/mon.csv", f"{out}/mon.csv"], capture_output=True)
    return rt.summarize(out)[0]


def num(x):
    return float(re.sub(r"[^0-9.]", "", str(x)) or 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default=time.strftime("p3_%Y%m%d_%H%M%S"))
    ap.add_argument("--scenarios", nargs="+", default=["bypass", "off", "on"], choices=list(rt.SCENARIOS))
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--delay", nargs="+", default=["0ms"], help="netem delay per direction, e.g. 40ms")
    ap.add_argument("--jitter", nargs="+", default=["0ms"], help="netem jitter, e.g. 15ms")
    ap.add_argument("--loss", nargs="+", default=["0%"], help="netem random loss, e.g. 2%%")
    ap.add_argument("--video", nargs="+", default=["8M"], help="UDP video rate, e.g. 4M 12M")
    ap.add_argument("--bulk-streams", nargs="+", type=int, default=[4], dest="bulk_streams")
    ap.add_argument("--bulk-ceil", nargs="+", default=["default"], dest="bulk_ceil",
                    help="policer ceiling for bulk (>= 9mbit); only affects the 'on' scenario")
    ap.add_argument("--cls-mode", nargs="+", default=["rules"], choices=["rules", "ml", "collect"], dest="cls_mode",
                    help="classifier mode; only affects the 'on' scenario")
    ap.add_argument("--voip-interval", nargs="+", default=["0.2"], dest="voip_interval",
                    help="seconds between VoIP pings (0.01 = ~100 packets/s)")
    ap.add_argument("--duration", type=int, default=0, help="video length in s (default: keep 30)")
    a = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("Run with sudo.")
    if a.duration:
        rt.DUR_VIDEO, rt.DUR_PING, rt.DUR_BULK = a.duration, a.duration + 5, max(5, a.duration - 10)

    qosi.SESSION = qosi.find_session()
    qosi.prepare()
    base = f"{ROOT}/results/{a.name}"
    os.makedirs(base, exist_ok=True)
    for n in ("srv-video", "srv-bulk"):
        rt.run(n, "pkill", "iperf3")
    servers = [subprocess.Popen(rt.vcmd(n) + ["iperf3", "-s", "-p", rt.PORT],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
               for n in ("srv-video", "srv-bulk")]
    time.sleep(1)

    rows = []
    combos = list(itertools.product(a.delay, a.jitter, a.loss, a.video, a.bulk_streams,
                                    a.bulk_ceil, a.cls_mode, a.voip_interval))
    est = len(combos) * len(a.scenarios) * a.runs * 50 / 60
    print(f"{len(combos)} condition(s) x {len(a.scenarios)} scenario(s) x {a.runs} run(s), about {est:.0f} min")
    try:
        for i, vals in enumerate(combos, 1):
            c = dict(zip(COND, vals))
            label = "_".join(f"{k}{v}" for k, v in c.items()).replace("%", "p")
            print(f"\n######## [{i}/{len(combos)}] {c}")
            netem(c["delay"], c["netem_jitter"], c["loss"])
            for scn in a.scenarios:
                runs = []
                for k in range(1, a.runs + 1):
                    print(f"--- {scn} run {k}/{a.runs}")
                    runs.append(one_run(scn, f"{base}/{label}/{scn}/run{k}", c))
                rows.append({**c, "scenario": scn,
                             **{m: sum(r[m] for r in runs) / len(runs) for m in KEYS}})
    finally:
        netem("0ms", "0ms", "0%")
        for n in ("srv-video", "srv-bulk"):
            rt.run(n, "pkill", "iperf3")
        for s in servers:
            s.terminate()
        qosi.set_mode("chain")
        for v in ("fw", "cls", "pol"):
            rt.vnf(v, "on")                           # leave the chain in its default state

    if rows:
        with open(f"{base}/sweep.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COND + ["scenario"] + KEYS)
            w.writeheader()
            w.writerows(rows)
        print(f"\nSaved {base}/sweep.csv")
        varied = [k for k in COND if len({r[k] for r in rows}) > 1]
        if len(varied) == 1 and varied[0] not in NO_GRAPH:
            try:
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib.pyplot as plt
                x = varied[0]
                fig, axs = plt.subplots(1, 4, figsize=(15, 3.6))
                for ax, (m, t) in zip(axs, [("rtt_avg", "VoIP RTT avg (ms)"), ("rtt_p95", "VoIP RTT p95 (ms)"),
                                            ("bulk_mbps", "Bulk (Mbit/s)"), ("video_loss", "Video loss (%)")]):
                    for scn in a.scenarios:
                        pts = sorted((num(r[x]), r[m]) for r in rows if r["scenario"] == scn)
                        ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", label=scn)
                    ax.set(xlabel=x, title=t)
                axs[0].legend()
                fig.tight_layout()
                fig.savefig(f"{base}/sweep_{x}.png", dpi=130)
                print(f"Saved sweep_{x}.png")
            except ImportError:
                print("matplotlib missing: graph skipped")
        else:
            print("Zero or several options vary: use sweep.csv (a graph needs exactly one varied numeric option).")
    if os.environ.get("SUDO_USER"):
        subprocess.run(["chown", "-R", os.environ["SUDO_USER"], f"{ROOT}/results"])


if __name__ == "__main__":
    main()
