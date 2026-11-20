#!/bin/bash
# QoSI TP3 setup: installs everything needed on a machine where CORE is already installed.
# Usage (from the repo root):  sudo bash setup.sh
set -u

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo bash setup.sh"; exit 1; }
cd "$(dirname "$0")"
REAL_USER="${SUDO_USER:-root}"
ok()   { echo "  [ok]   $*"; }
warn() { echo "  [WARN] $*"; WARNINGS=$((WARNINGS+1)); }
WARNINGS=0

echo "== 1. Environment"
command -v apt-get >/dev/null || { echo "This script needs apt (Debian/Ubuntu)."; exit 1; }
IS_WSL=0; grep -qi microsoft /proc/version && IS_WSL=1
[ $IS_WSL -eq 1 ] && ok "WSL detected" || ok "native Linux / VM detected ($(uname -m))"
command -v core-daemon >/dev/null && ok "CORE found: $(core-daemon --version 2>&1 | head -1)" \
    || warn "core-daemon not found: install CORE first"
command -v core-gui >/dev/null && ok "core-gui found" || warn "core-gui not found"

echo "== 2. Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq docker.io iperf3 traceroute curl iproute2 iptables tcpdump \
    python3 python3-pip python3-matplotlib util-linux ca-certificates >/dev/null \
    && ok "base packages" || warn "some base packages failed to install"

# Compose and buildx: package names differ between Ubuntu releases
if ! docker compose version >/dev/null 2>&1; then
    apt-get install -y -qq docker-compose-v2 >/dev/null 2>&1 \
        || apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 \
        || warn "could not install the docker compose plugin (see README)"
fi
docker buildx version >/dev/null 2>&1 || apt-get install -y -qq docker-buildx >/dev/null 2>&1 \
    || apt-get install -y -qq docker-buildx-plugin >/dev/null 2>&1 \
    || warn "buildx not installed (builds still work with the legacy builder)"
docker compose version >/dev/null 2>&1 && ok "$(docker compose version)" || warn "docker compose missing"

echo "== 3. Docker daemon"
if [ "$(ps -p 1 -o comm= 2>/dev/null)" = "systemd" ]; then
    systemctl enable --now docker >/dev/null 2>&1
else
    service docker start >/dev/null 2>&1
    [ $IS_WSL -eq 1 ] && warn "WSL without systemd: add [boot] systemd=true to /etc/wsl.conf, run 'wsl --shutdown' in PowerShell, then rerun this script"
fi
sleep 2
docker info >/dev/null 2>&1 && ok "docker daemon running" || warn "docker daemon not running"
if [ "$REAL_USER" != "root" ]; then
    usermod -aG docker "$REAL_USER" && ok "$REAL_USER added to the docker group (takes effect on next login)"
fi

echo "== 4. Kernel modules"
MODS="sch_netem sch_htb sch_fq_codel cls_u32 xt_DSCP xt_conntrack nf_conntrack nfnetlink_queue nfnetlink_log"
: > /etc/modules-load.d/qosi.conf 2>/dev/null
for m in $MODS; do
    if modprobe "$m" 2>/dev/null; then ok "$m"; echo "$m" >> /etc/modules-load.d/qosi.conf
    else warn "module $m not available (may be built into the kernel; the VNF tests will tell)"; fi
done

echo "== 5. Host forwarding policy (Docker sets FORWARD to DROP, which breaks CORE links)"
iptables -P FORWARD ACCEPT && ok "FORWARD policy ACCEPT (the orchestrator reapplies it)"

echo "== 6. Repo files"
find . -path ./.git -prune -o -type f \( -name '*.sh' -o -name '*.py' -o -name '*.yml' -o -name '*.json' -o -name Dockerfile \) \
    -exec sed -i 's/\r$//' {} + && ok "line endings converted to LF"
chmod +x vnfs/*/*.sh 2>/dev/null
mkdir -p results

echo "== 7. Docker images (VNFs, Prometheus, Grafana)"
if docker info >/dev/null 2>&1 && ! ip link show docker0 >/dev/null 2>&1; then
    # CORE can delete Docker's bridge; builds then fail with "network bridge not found"
    if [ "$(ps -p 1 -o comm= 2>/dev/null)" = "systemd" ]; then systemctl restart docker
    else service docker restart >/dev/null 2>&1; fi
    sleep 2
    ip link show docker0 >/dev/null 2>&1 && ok "docker0 bridge was missing: restarted docker" \
        || warn "docker0 bridge missing even after restarting docker"
fi
if docker info >/dev/null 2>&1; then
    docker compose build base >/dev/null 2>&1 && docker compose build 2>&1 | tail -n 3
    docker images --format '{{.Repository}}' | grep -c '^qosi-' | xargs -I{} echo "  qosi images built: {} (expected 5)"
    docker compose pull prometheus grafana >/dev/null 2>&1 && ok "prometheus and grafana images pulled" \
        || warn "could not pull prometheus/grafana (network?)"
else
    warn "skipping image builds: docker is not running"
fi

echo
echo "== Done with $WARNINGS warning(s)."
cat <<'EOF'

Next steps:
  1. Log out and back in (or 'newgrp docker') so the docker group applies.
  2. sudo docker compose up -d                 # Prometheus + Grafana
  3. Open topology/chain.xml in core-gui and press play
  4. sudo python3 orchestrator/qosi.py chain
  5. sudo bash vnfs/mon/connect.sh             # prints: exporter: 200
EOF