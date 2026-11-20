#!/usr/bin/env python3
"""Orchestrator: switches the path between the VNF chain and the bypass link.
The chain routes are already set by the topology (StaticRoute in chain.xml) when play is
pressed, so this is only needed to change path; the test scripts call it themselves.
Usage: sudo python3 orchestrator/qosi.py chain|bypass
"""
import glob
import os
import subprocess
import sys

VNFS = ["vnf-fw", "vnf-cls", "vnf-pol", "vnf-mon"]
CLIENTS, SERVERS = "10.0.1.0/24", "10.0.20.0/24"
SESSION = None  # set in main


def find_session():
    """Return the newest CORE session folder that contains this topology."""
    for d in sorted(glob.glob("/tmp/pycore.*"), key=os.path.getmtime, reverse=True):
        if os.path.exists(f"{d}/r1.pid") and os.path.exists(f"{d}/r2.pid"):
            return d
    sys.exit("No active CORE session: open topology/chain.xml and press play.")


def run_in(node, *cmd):
    """Run a command inside a node (docker exec for VNFs, vcmd for CORE nodes)."""
    if node in VNFS:
        full = ["docker", "exec", node, *cmd]
    else:
        full = ["vcmd", "-c", f"{SESSION}/{node}", "--", *cmd]
    r = subprocess.run(full, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"Failed on {node}: {' '.join(cmd)}\n{(r.stderr or r.stdout).strip()}")
    return r.stdout


def route(node, dest, via):
    run_in(node, "ip", "route", "replace", dest, "via", via)


def prepare():
    """Host fix, forwarding in the containers, and the VNF-to-VNF routes."""
    # Docker sets the host FORWARD policy to DROP, which breaks CORE links
    subprocess.run(["iptables", "-P", "FORWARD", "ACCEPT"], check=True)
    for v in VNFS:
        run_in(v, "sysctl", "-w", "net.ipv4.ip_forward=1")
    # Chain routes: forward (to servers) and return (to clients), always set
    route("vnf-fw",  SERVERS, "10.0.100.6");  route("vnf-fw",  CLIENTS, "10.0.100.1")
    route("vnf-cls", SERVERS, "10.0.100.10"); route("vnf-cls", CLIENTS, "10.0.100.5")
    route("vnf-pol", SERVERS, "10.0.100.14"); route("vnf-pol", CLIENTS, "10.0.100.9")
    route("vnf-mon", SERVERS, "10.0.100.18"); route("vnf-mon", CLIENTS, "10.0.100.13")


def set_mode(mode):
    """Only r1 and r2 change between modes."""
    if mode == "chain":
        route("r1", SERVERS, "10.0.100.2")     # r1 -> vnf-fw
        route("r2", CLIENTS, "10.0.100.17")    # r2 -> vnf-mon (return)
    else:
        route("r1", SERVERS, "10.0.200.2")     # r1 -> r2 over the bypass link
        route("r2", CLIENTS, "10.0.200.1")     # r2 -> r1 over the bypass link
    print("r1:", run_in("r1", "ip", "route", "get", "10.0.20.10").splitlines()[0])
    print("r2:", run_in("r2", "ip", "route", "get", "10.0.1.10").splitlines()[0])


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("chain", "bypass"):
        sys.exit("Usage: sudo python3 orchestrator/qosi.py chain|bypass")
    SESSION = find_session()
    prepare()
    set_mode(sys.argv[1])
    print(f"Mode {sys.argv[1]} active ({SESSION})")