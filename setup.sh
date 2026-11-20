#!/bin/bash
# QoSI TP3 setup: turns a fresh Ubuntu 22.04 or newer machine (VM, or WSL 2 with systemd) into a working
# lab: CORE, Docker, kernel modules, the VNF images, Prometheus and Grafana. Safe to re-run.
# Usage (from the repo root):  sudo bash setup.sh
# Optional: CORE_VERSION=9.2.0 sudo -E bash setup.sh   (CORE release to install if missing)
set -u

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo bash setup.sh"; exit 1; }
cd "$(dirname "$0")"
REAL_USER="${SUDO_USER:-root}"
CORE_VERSION="${CORE_VERSION:-9.2.1}"
ok()   { echo "  [ok]   $*"; }
warn() { echo "  [WARN] $*"; WARNINGS=$((WARNINGS+1)); }
WARNINGS=0
has_systemd() { [ "$(ps -p 1 -o comm= 2>/dev/null)" = "systemd" ]; }

echo "== 1. Environment"
command -v apt-get >/dev/null || { echo "This script needs apt (Ubuntu)."; exit 1; }
. /etc/os-release
if [ "${ID:-}" = "ubuntu" ] && dpkg --compare-versions "${VERSION_ID:-0}" ge 22.04; then
    ok "Ubuntu $VERSION_ID"
else
    warn "${PRETTY_NAME:-unknown OS}: tested on Ubuntu 22.04 and newer, this may fail"
fi
[ "$(dpkg --print-architecture)" = "amd64" ] || { echo "CORE packages are amd64 only."; exit 1; }
IS_WSL=0; grep -qi microsoft /proc/version && IS_WSL=1
[ $IS_WSL -eq 1 ] && ok "WSL detected" || ok "native Linux / VM detected"
has_systemd && ok "systemd running" \
    || warn "no systemd: services must be started by hand (WSL: add [boot] systemd=true to /etc/wsl.conf, run 'wsl --shutdown' in PowerShell, rerun this script)"

echo "== 2. Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq docker.io iperf3 traceroute curl wget iproute2 iptables iputils-ping tcpdump \
    python3 python3-pip python3-venv python3-tk python3-matplotlib xterm psmisc \
    util-linux ca-certificates >/dev/null \
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

echo "== 3. CORE"
if command -v core-daemon >/dev/null; then
    ok "CORE already installed: $(core-daemon --version 2>&1 | head -1)"
else
    PKG="core_${CORE_VERSION}_amd64.deb"
    URL="https://github.com/coreemu/core/releases/download/release-${CORE_VERSION}/${PKG}"
    echo "  installing CORE $CORE_VERSION (a few minutes)..."
    if wget -q -O "/tmp/$PKG" "$URL" && apt-get install -y -qq "/tmp/$PKG" >/dev/null; then
        ok "CORE $CORE_VERSION installed"
    else
        warn "CORE install failed (download: $URL)"
    fi
    rm -f "/tmp/$PKG"
fi
command -v core-gui >/dev/null && ok "core-gui found" || warn "core-gui not found"
if command -v core-daemon >/dev/null; then
    if has_systemd; then
        systemctl enable --now core-daemon >/dev/null 2>&1 && ok "core-daemon enabled and running" \
            || warn "could not start core-daemon (journalctl -u core-daemon)"
    else
        warn "no systemd: start CORE by hand with 'sudo core-daemon' before opening core-gui"
    fi
fi

echo "== 4. Docker daemon"
if has_systemd; then
    systemctl enable --now docker >/dev/null 2>&1
else
    service docker start >/dev/null 2>&1
fi
sleep 2
docker info >/dev/null 2>&1 && ok "docker daemon running" || warn "docker daemon not running"
if [ "$REAL_USER" != "root" ]; then
    usermod -aG docker "$REAL_USER" && ok "$REAL_USER added to the docker group (takes effect on next login)"
fi

echo "== 5. Kernel modules"
MODS="sch_netem sch_htb sch_fq_codel cls_u32 xt_DSCP xt_conntrack nf_conntrack nfnetlink_queue nfnetlink_log"
: > /etc/modules-load.d/qosi.conf 2>/dev/null
for m in $MODS; do
    if modprobe "$m" 2>/dev/null; then ok "$m"; echo "$m" >> /etc/modules-load.d/qosi.conf
    else warn "module $m not available (may be built into the kernel; the VNF tests will tell)"; fi
done

echo "== 6. Host forwarding policy (Docker sets FORWARD to DROP, which breaks CORE links)"
iptables -P FORWARD ACCEPT && ok "FORWARD policy ACCEPT (the topology sets it again on every play)"

echo "== 7. Repo files"
find . -path ./.git -prune -o -type f \( -name '*.sh' -o -name '*.py' -o -name '*.yml' -o -name '*.json' -o -name Dockerfile \) \
    -exec sed -i 's/\r$//' {} + && ok "line endings converted to LF"
chmod +x vnfs/*/*.sh 2>/dev/null
mkdir -p results && chown "$REAL_USER" results

echo "== 8. Docker images and monitoring (VNFs, Prometheus, Grafana)"
if docker info >/dev/null 2>&1 && ! ip link show docker0 >/dev/null 2>&1; then
    # CORE can delete Docker's bridge; builds then fail with "network bridge not found"
    if has_systemd; then systemctl restart docker; else service docker restart >/dev/null 2>&1; fi
    sleep 2
    ip link show docker0 >/dev/null 2>&1 && ok "docker0 bridge was missing: restarted docker" \
        || warn "docker0 bridge missing even after restarting docker"
fi
if docker info >/dev/null 2>&1; then
    echo "  building the VNF images (the first time takes several minutes)..."
    docker compose build base >/dev/null 2>&1 && docker compose build 2>&1 | tail -n 3
    n=$(docker images --format '{{.Repository}}' | grep -c '^qosi-')
    [ "$n" -eq 5 ] && ok "qosi images built: $n" || warn "qosi images built: $n (expected 5)"
    docker compose up -d prometheus grafana >/dev/null 2>&1 \
        && ok "Prometheus (:9090) and Grafana (:3000) running" \
        || warn "could not start Prometheus/Grafana (network?)"
else
    warn "skipping images and monitoring: docker is not running"
fi

echo
echo "== Done with $WARNINGS warning(s)."
cat <<'EOF'

Next steps:
  1. Log out and back in (or 'newgrp docker') so the docker group applies.
  2. Open topology/chain.xml in core-gui and press play (the chain is ready, no other command)
  3. sudo bash vnfs/cls/test.sh                # quick check that traffic goes through the chain
  4. Grafana: http://localhost:3000
EOF
