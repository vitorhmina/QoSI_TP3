#!/usr/bin/env python3
"""Classification accuracy under random traffic: rules vs ML on the same traffic (same seed).
Usage: sudo python3 tests/cls_accuracy.py [--duration 180] [--seed N] [--modes rules ml]
For each mode the classifier records every flow it decides with its true class (the server it
talks to), while tests/traffic.py generates random VoIP / video / bulk traffic.
Results: results/cls_acc_<time>_seed<N>/ with summary.csv, <mode>/flows.csv (every decision),
<mode>/errors.csv (misclassified flows) and the traffic log of each run.
"""
import argparse
import csv
import glob
import os
import random
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLASSES = ["voip", "video", "bulk"]
VNF_MODE = {"rules": "collect", "ml": "ml"}    # collect = rules decision, recorded per flow
FLOWS = "/var/log/cls.flows.csv"


def sh(*cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"failed: {' '.join(cmd)}\n{(r.stderr or r.stdout).strip()}")
    return r


def preflight():
    if os.geteuid() != 0:
        sys.exit("Run with sudo.")
    sessions = sorted(glob.glob("/tmp/pycore.*"), key=os.path.getmtime, reverse=True)
    if not sessions:
        sys.exit("No CORE session: open topology/chain.xml and press play.")
    if sh("docker", "exec", "vnf-cls", "true", check=False).returncode:
        sys.exit("vnf-cls is not running: press play in CORE.")
    route = sh("vcmd", "-c", f"{sessions[0]}/r1", "--", "ip", "route", "get", "10.0.20.10",
               check=False).stdout
    if "10.0.100.2" not in route:
        sys.exit("Traffic is not going through the VNF chain: sudo python3 orchestrator/qosi.py chain")


def run_mode(mode, a, out):
    os.makedirs(out, exist_ok=True)
    sh("docker", "exec", "vnf-cls", "python3", "/app/vnf.py", "on", VNF_MODE[mode])
    sh("docker", "exec", "vnf-cls", "rm", "-f", FLOWS)
    sh("docker", "exec", "vnf-cls", "conntrack", "-F", check=False)   # no decisions carried over
    time.sleep(1)
    print(f"\n=== {mode}: {a.duration}s of random traffic (seed {a.seed}) ===", flush=True)
    subprocess.run([sys.executable, os.path.join(ROOT, "tests", "traffic.py"), "--duration",
                    str(a.duration), "--seed", str(a.seed), "--out", os.path.join(out, "traffic")])
    time.sleep(2)
    if sh("docker", "cp", f"vnf-cls:{FLOWS}", os.path.join(out, "flows.csv"), check=False).returncode:
        print(f"no flows recorded in {mode} mode")
        return []
    with open(os.path.join(out, "flows.csv"), newline="") as f:
        return [r for r in csv.DictReader(f) if r["label"] in CLASSES]


def metrics(rows):
    conf = {t: {p: 0 for p in CLASSES} for t in CLASSES}
    for r in rows:
        conf[r["label"]][r["pred"]] += 1
    m = {"flows": len(rows), "accuracy": sum(conf[c][c] for c in CLASSES) / max(1, len(rows))}
    for c in CLASSES:
        tp = conf[c][c]
        pred_n = sum(conf[t][c] for t in CLASSES)
        true_n = sum(conf[c].values())
        p = tp / pred_n if pred_n else 0.0
        rc = tp / true_n if true_n else 0.0
        m[f"{c}_n"], m[f"{c}_precision"], m[f"{c}_recall"] = true_n, p, rc
        m[f"{c}_f1"] = 2 * p * rc / (p + rc) if p + rc else 0.0
    m["macro_f1"] = sum(m[f"{c}_f1"] for c in CLASSES) / len(CLASSES)
    return m, conf


def show(mode, m, conf, rows, out):
    print(f"\n--- {mode}: {m['flows']} flows, accuracy {m['accuracy'] * 100:.1f}%, "
          f"macro F1 {m['macro_f1']:.3f}")
    print("confusion (rows = true, cols = predicted)")
    print("         " + "".join(f"{c:>8}" for c in CLASSES) + "   precision  recall")
    for c in CLASSES:
        print(f"{c:8} " + "".join(f"{conf[c][p]:8}" for p in CLASSES)
              + f"   {m[f'{c}_precision'] * 100:8.1f}%  {m[f'{c}_recall'] * 100:5.1f}%")
    errors = [r for r in rows if r["label"] != r["pred"]]
    with open(os.path.join(out, "errors.csv"), "w", newline="") as f:
        if errors:
            w = csv.DictWriter(f, fieldnames=list(errors[0]))
            w.writeheader()
            w.writerows(errors)
    for r in errors[:10]:
        print(f"  wrong: {r['flow']:40} true {r['label']:5} -> {r['pred']}")
    if len(errors) > 10:
        print(f"  ... {len(errors) - 10} more in {out}/errors.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=180, help="seconds of traffic per mode")
    ap.add_argument("--seed", type=int, default=None, help="same seed = same traffic schedule")
    ap.add_argument("--modes", nargs="+", default=["rules", "ml"], choices=list(VNF_MODE))
    ap.add_argument("--name", default=None)
    a = ap.parse_args()
    preflight()
    a.seed = a.seed if a.seed is not None else random.randrange(1, 10**6)
    base = os.path.join(ROOT, "results", a.name or f"cls_acc_{time.strftime('%Y%m%d_%H%M%S')}_seed{a.seed}")

    summary = []
    for mode in a.modes:
        rows = run_mode(mode, a, os.path.join(base, mode))
        m, conf = metrics(rows)
        show(mode, m, conf, rows, os.path.join(base, mode))
        summary.append({"mode": mode, "seed": a.seed, "duration": a.duration,
                        **{k: round(v, 4) if isinstance(v, float) else v for k, v in m.items()}})
    sh("docker", "exec", "vnf-cls", "python3", "/app/vnf.py", "on", "rules")

    with open(os.path.join(base, "summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)
    print(f"\n{'mode':6} {'flows':>6} {'accuracy':>9} {'macro F1':>9}  "
          + "  ".join(f"{c + ' F1':>8}" for c in CLASSES))
    for s in summary:
        print(f"{s['mode']:6} {s['flows']:6} {s['accuracy'] * 100:8.1f}% {s['macro_f1']:9.3f}  "
              + "  ".join(f"{s[c + '_f1']:8.3f}" for c in CLASSES))
    print(f"\nSaved {base}/summary.csv (vnf-cls is back in rules mode)")


if __name__ == "__main__":
    main()
