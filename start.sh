#!/bin/bash
set -e

export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
export TZ="Asia/Kolkata"

PORT="${PORT:-10000}"

echo "=== [HermesTerminal Startup with Tailscale] ==="

# 0. Tailscale setup
if [ -n "$TAILSCALE_AUTHKEY" ]; then
    echo ">> Tailscale join करत आहोत..."
    mkdir -p /var/run/tailscale /var/lib/tailscale
    tailscaled --state=/var/lib/tailscale/tailscaled.state \
        --tun=userspace-networking \
        --socks5-server=localhost:1055 > /var/log/tailscaled.log 2>&1 &
    sleep 2
    tailscale up \
        --authkey="$TAILSCALE_AUTHKEY" \
        --hostname="${TAILSCALE_HOSTNAME:-hermes-cloud}" \
        --accept-routes=false \
        --ssh=true || echo "!! Tailscale up fail झाले"
    TS_IP=$(tailscale ip -4 2>/dev/null || echo "??")
    echo ">> Tailscale IP: $TS_IP"
fi

# Set root password if needed
if [ -n "$SSH_PASSWORD" ]; then
    echo "root:$SSH_PASSWORD" | chpasswd 2>/dev/null || true
fi

# Start SSH server in background if needed
if command -v sshd >/dev/null 2>&1; then
    mkdir -p /var/run/sshd
    /usr/sbin/sshd -d -e &
fi

# Start Python native terminal server directly on $PORT
echo ">> Starting Python Native Web Terminal & Health Server on port $PORT..."
python3 server.py
