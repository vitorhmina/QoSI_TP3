#!/usr/bin/env python3
"""Classification VNF: Scapy + NFQUEUE, per flow.
Scapy inspects new flows and sets a mark, the packet is re-injected (repeat), the mark is saved
in conntrack, and iptables applies the DSCP to every later packet in-kernel.
Modes (switchable live with `mode <name>`, no restart):
  rules    decides on the first packet (protocol / RTP heuristics)
  ml       watches the first packets of the flow (sent as best effort meanwhile), then a decision
           tree (scikit-learn) trained on flow behaviour decides (logic in /app/ml)
  collect  same window, decided by the rules, and every flow is recorded with its true class
           to /var/log/cls.flows.csv to retrain the model
Usage: python3 /app/vnf.py on [mode] | off | status | mode [mode] | train | logs
"""
import csv
import glob
import json
import os
import subprocess
import sys
import threading
import time

ML_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml")
sys.path.insert(0, ML_DIR)

LOGFILE = "/var/log/cls.log"
STATS = "/var/log/cls.stats.json"
MODEFILE = "/var/log/cls.mode"
FLOWS_CSV = "/var/log/cls.flows.csv"
MODEL = os.path.join(ML_DIR, "model.joblib")
MODEL_META = os.path.join(ML_DIR, "model.meta.json")
IN_IF = "eth0"      # only traffic coming from the client side is classified
QUEUE = 1
PENDING = 4         # mark for packets of a flow still being observed: DF, not saved in conntrack
CLASSES = {"voip": ("EF", 46, 1), "video": ("AF31", 26, 2), "bulk": ("BE", 0, 3)}  # label, dscp, mark
MODES = ("rules", "ml", "collect")
# ground truth for collect/evaluation only (the server each app talks to), never a model feature
TRUTH = {"10.0.20.10": "voip", "10.0.20.20": "video", "10.0.20.30": "bulk"}


def truth_of(key):
    """True class of a flow. In the lab video is UDP only, so a TCP connection to srv-video is
    iperf3's control channel: a few small messages, best effort like any other control traffic."""
    proto, _s, _sp, dst, _dp = key
    t = TRUTH.get(dst, "")
    return "bulk" if t == "video" and proto == "tcp" else t

_lock = threading.Lock()


def emit(line):
    with _lock:
        with open(LOGFILE, "a") as f:
            f.write(line + "\n")
        try:
            with open("/proc/1/fd/1", "w") as out:   # shows up in `docker logs`
                out.write(line + "\n")
        except OSError:
            pass


def ipt(*args):
    subprocess.run(["iptables", "-t", "mangle", *args], check=True)


def off():
    subprocess.run(["iptables", "-t", "mangle", "-F", "FORWARD"], capture_output=True)
    subprocess.run(["pkill", "-f", "vnf.py daemon"], capture_output=True)
    emit("cls: off")


def on(mode=None):
    mode = mode or os.environ.get("CLS_MODE", "rules")
    if mode not in MODES:
        sys.exit(f"mode must be one of {MODES}")
    subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True, capture_output=True)
    off()
    with open(MODEFILE, "w") as f:
        f.write(mode)
    base = ["-A", "FORWARD", "-i", IN_IF]
    # 1. known flow: restore its mark from conntrack (only if no mark yet, so a mark set by
    #    Scapy on a re-injected packet is not overwritten)
    ipt(*base, "-m", "mark", "--mark", "0", "-j", "CONNMARK", "--restore-mark")
    # 2. marked packet: remember the decision, set DSCP, accept (stays in the kernel)
    for _label, dscp, mark in CLASSES.values():
        ipt(*base, "-m", "mark", "--mark", str(mark), "-j", "CONNMARK", "--save-mark")
        ipt(*base, "-m", "mark", "--mark", str(mark), "-j", "DSCP", "--set-dscp", str(dscp))
        ipt(*base, "-m", "mark", "--mark", str(mark), "-j", "ACCEPT")
    # 3. flow still being observed (ml/collect): best effort for now, the next packet comes back
    ipt(*base, "-m", "mark", "--mark", str(PENDING), "-j", "DSCP", "--set-dscp", "0")
    ipt(*base, "-m", "mark", "--mark", str(PENDING), "-j", "ACCEPT")
    # 4. unmarked (new or still undecided flow): ask Scapy; fail open if the daemon is down
    ipt(*base, "-j", "NFQUEUE", "--queue-num", str(QUEUE), "--queue-bypass")
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "daemon"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    emit(f"cls: classification active (Scapy + NFQUEUE, mode {mode})")


def read_mode():
    try:
        return open(MODEFILE).read().strip()
    except OSError:
        return "rules"


def classify_rules(ip):
    from scapy.layers.inet import ICMP, UDP
    if ip.haslayer(ICMP):
        return "voip"                                  # ICMP stands in for VoIP probes
    if ip.haslayer(UDP):
        udp = ip[UDP]
        data = bytes(udp.payload)
        if len(data) >= 12 and data[0] >> 6 == 2 and 16384 <= udp.dport <= 32767:
            return "voip"                              # RTP: version 2 + RTP port range
        return "video"                                 # other UDP = streaming
    return "bulk"                                      # TCP and anything else = best effort


def flow_key(ip):
    from scapy.layers.inet import ICMP, TCP, UDP
    if ip.haslayer(TCP):
        return ("tcp", ip.src, ip[TCP].sport, ip.dst, ip[TCP].dport)
    if ip.haslayer(UDP):
        return ("udp", ip.src, ip[UDP].sport, ip.dst, ip[UDP].dport)
    if ip.haslayer(ICMP):                              # the echo id separates ping sessions,
        return ("icmp", ip.src, ip[ICMP].id or 0, ip.dst, 0)   # as conntrack does
    return (str(ip.proto), ip.src, 0, ip.dst, 0)


def record(mode, key, truth, pred, vec):
    """One row per decided flow (ml / collect): features, true class and decision."""
    from features import FEATURES
    new = not os.path.exists(FLOWS_CSV)
    with open(FLOWS_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "mode", "flow", "label", "pred", *FEATURES])
        w.writerow([time.strftime("%H:%M:%S"), mode, "%s %s:%s>%s:%s" % key, truth, pred, *vec])


def daemon():
    from netfilterqueue import NetfilterQueue
    from scapy.layers.inet import IP
    from features import Flow
    from model import Model
    clf = Model(MODEL)
    st = {"mode": None, "mtime": None, "checked": 0.0, "warned": False}
    flows = {}                                         # flow key -> class (decision cache)
    pending = {}                                       # flow key -> Flow still being observed
    counts = {c: 0 for c in CLASSES}

    def refresh(now):
        """Picks up a mode switch or a retrained model without restarting (checked every 0.5 s)."""
        if now - st["checked"] < 0.5:
            return
        st["checked"] = now
        try:
            mt = os.stat(MODEFILE).st_mtime_ns
        except OSError:
            mt = 0
        if mt != st["mtime"]:
            st["mtime"], st["mode"] = mt, read_mode()
            flows.clear()
            pending.clear()
            emit(f"cls: mode {st['mode']}")
        if clf.reload():
            emit(f"cls: model loaded ({clf.describe()})")
        if st["mode"] == "ml" and not clf.ok and not st["warned"]:
            st["warned"] = True
            emit(f"cls: no model at {MODEL}, ml mode falls back to the rules")

    def decide(ip, key, ts):
        """Returns (class, how), or None while the flow is still being observed."""
        if st["mode"] == "rules":
            return classify_rules(ip), "rules"
        fl = pending.get(key)
        if fl is None:
            if len(pending) > 20000:                   # flows that never fill the window
                pending.clear()
            fl = pending[key] = Flow(key[0])
        fl.add(ts, ip.len)
        if not fl.ready():
            return None
        del pending[key]
        vec = fl.vector()
        if st["mode"] == "ml" and clf.ok:
            cls, p = clf.predict(vec)
            how = f"ml p={p:.2f} n={len(fl.sizes)}"
        else:
            cls, how = classify_rules(ip), f"rules n={len(fl.sizes)}"
        record(st["mode"], key, truth_of(key), cls, vec)
        return cls, how

    def handle(pkt):
        try:
            now = time.time()
            refresh(now)
            ip = IP(pkt.get_payload())
            key = flow_key(ip)
            cls = flows.get(key)
            if cls is None:
                try:
                    ts = pkt.get_timestamp() or now   # kernel arrival time when available
                except AttributeError:
                    ts = now
                got = decide(ip, key, ts)
                if got is None:                        # undecided: sent as best effort for now
                    pkt.set_mark(PENDING)
                    pkt.repeat()
                    return
                cls, how = got
                if len(flows) > 50000:
                    flows.clear()
                flows[key] = cls
                counts[cls] += 1
                label, dscp, _ = CLASSES[cls]
                p, s, sp, d, dp = key
                emit(f"{label:4} dscp={dscp:<2} {time.strftime('%H:%M:%S')} {p} {s}:{sp} > {d}:{dp}"
                     f"  [{how}]")
            pkt.set_mark(CLASSES[cls][2])
            pkt.repeat()                               # re-run the chain with the mark set
            return
        except Exception as e:                         # never drop a packet because of a bug
            emit(f"cls: error {type(e).__name__}: {e}")
        pkt.accept()

    def write_stats():
        while True:
            time.sleep(1)
            with open(STATS, "w") as f:
                json.dump({c: {"flows": n} for c, n in dict(counts).items()}, f)

    threading.Thread(target=write_stats, daemon=True).start()
    refresh(time.time())
    emit(f"cls: classifier daemon started (mode {st['mode']})")
    nfq = NetfilterQueue()
    nfq.bind(QUEUE, handle, max_len=4096)
    nfq.run()


def daemon_running():
    return subprocess.run(["pgrep", "-f", "vnf.py daemon"], capture_output=True).returncode == 0


def mode(new=None):
    """No argument: print the mode. With a mode: switch live, without restarting the daemon."""
    if new is None:
        print(read_mode())
        return
    if new not in MODES:
        sys.exit(f"mode must be one of {MODES}")
    if not daemon_running():
        on(new)
        return
    with open(MODEFILE, "w") as f:
        f.write(new)
    # forget the per-flow decisions kept in conntrack so running flows are reclassified too
    try:
        flushed = subprocess.run(["conntrack", "-F"], capture_output=True).returncode == 0
    except FileNotFoundError:
        flushed = False
    if not flushed:
        print("warning: conntrack not available, flows already running keep their class")
    if new == "ml" and not os.path.exists(MODEL):
        print(f"warning: no model at {MODEL}, run 'vnf.py train' (falls back to the rules)")
    emit(f"cls: switching to mode {new} (live)")
    print(f"mode {new}")


def train():
    """Retrains with the synthetic flows plus the ones recorded here; the daemon reloads it."""
    data = sorted(glob.glob(os.path.join(ML_DIR, "data", "*.csv")))
    if os.path.exists(FLOWS_CSV):
        data.append(FLOWS_CSV)
    subprocess.run([sys.executable, os.path.join(ML_DIR, "train.py"), "--out", MODEL,
                    "--data", *data], check=True)


def logs():
    subprocess.run(["tail", "-n", "20", "-F", LOGFILE])


def status():
    print(f"--- mode: {read_mode()} (daemon {'running' if daemon_running() else 'NOT running'})",
          flush=True)
    print("--- mangle rules (DSCP rules = packets per class, NFQUEUE = packets sent to Scapy)", flush=True)
    subprocess.run(["iptables", "-t", "mangle", "-L", "FORWARD", "-n", "-v", "-x"])
    print("--- flows classified by Scapy", flush=True)
    try:
        for c, v in json.load(open(STATS)).items():
            print(f"{c:6} dscp={CLASSES[c][1]:<2} flows={v['flows']}")
    except (OSError, ValueError):
        print("no stats yet")
    try:
        with open(MODEL_META) as f:
            meta = json.load(f)
        mt = meta["metrics"]
        print(f"--- ml model: {meta['model']}, {mt['train_flows']} training flows, holdout accuracy "
              f"{mt['accuracy'] * 100:.1f}%, trained {mt['trained_at']}")
    except (OSError, ValueError, KeyError):
        print("--- ml model: none")
    try:   # live accuracy: decisions vs the true class of the server each flow went to
        acc = {}
        with open(FLOWS_CSV, newline="") as f:
            for r in csv.DictReader(f):
                if r["label"]:
                    ok, n = acc.get(r["mode"], (0, 0))
                    acc[r["mode"]] = (ok + (r["label"] == r["pred"]), n + 1)
        for m, (ok, n) in acc.items():
            print(f"--- live accuracy in {m} mode: {ok}/{n} flows = {ok / n * 100:.1f}%")
    except (OSError, KeyError):
        pass


if __name__ == "__main__":
    cmds = {"on": on, "off": off, "status": status, "mode": mode, "train": train,
            "logs": logs, "daemon": daemon}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit("Usage: vnf.py on [rules|ml|collect] | off | status | mode [rules|ml|collect]"
                 " | train | logs")
    if sys.argv[1] in ("on", "mode"):
        cmds[sys.argv[1]](sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        cmds[sys.argv[1]]()
