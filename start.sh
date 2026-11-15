#!/bin/bash
# Always-on box. SSH is reachable ONLY through your Tailscale network, no login/password.
export TZ="${TZ:-Asia/Kolkata}"
export HOME=/root
: "${TAILSCALE_AUTHKEY:?Set TAILSCALE_AUTHKEY env var}"
HOST="${TAILSCALE_HOSTNAME:-hermes-cloud}"
STATE_DIR="${TS_STATE_DIR:-/var/lib/tailscale}"
mkdir -p /var/run/tailscale /var/run/sshd "$STATE_DIR"

# --- sshd: bound to localhost only; tailnet is the security boundary ---
passwd -d root >/dev/null 2>&1
cat > /etc/ssh/sshd_config.hermes <<CFG
Port 2222
ListenAddress 127.0.0.1
HostKey /etc/ssh/ssh_host_ed25519_key
PermitRootLogin yes
PasswordAuthentication yes
PermitEmptyPasswords yes
UsePAM no
PrintMotd no
CFG
[ -f /etc/ssh/ssh_host_ed25519_key ] || ssh-keygen -q -t ed25519 -N "" -f /etc/ssh/ssh_host_ed25519_key
start_sshd() { pgrep -f "sshd -f /etc/ssh/sshd_config.hermes" >/dev/null || /usr/sbin/sshd -f /etc/ssh/sshd_config.hermes; }

start_ts() {
  pkill tailscaled 2>/dev/null; sleep 1
  tailscaled --state="$STATE_DIR/tailscaled.state" \
             --tun=userspace-networking \
             --socks5-server=localhost:1055 >/var/log/tailscaled.log 2>&1 &
  sleep 3
  tailscale up --authkey="$TAILSCALE_AUTHKEY" --hostname="$HOST" \
               --accept-dns=false --accept-routes=false \
    || echo "!! tailscale up failed, will retry"
  # expose local sshd as port 22 on the tailnet only
  tailscale serve --bg --tcp=22 tcp://localhost:2222 || echo "!! tailscale serve failed"
  echo ">> Tailscale IP: $(tailscale ip -4 2>/dev/null)"
}

python3 /app/health.py &
start_sshd
start_ts

fails=0
while true; do   # watchdog
  if tailscale status >/dev/null 2>&1 && pgrep -x tailscaled >/dev/null; then fails=0
  else fails=$((fails+1)); [ "$fails" -ge 3 ] && { echo "!! restarting tailscaled"; start_ts; fails=0; }; fi
  start_sshd
  pgrep -f health.py >/dev/null || { python3 /app/health.py & }
  sleep 20
done
