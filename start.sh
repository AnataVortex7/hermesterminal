#!/bin/bash
set -e

export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
export TZ="Asia/Kolkata"

PORT="${PORT:-10000}"

echo "=== [HermesTerminal Python Native Startup] ==="

# Set root password if needed
if [ -n "$SSH_PASSWORD" ]; then
    echo "root:$SSH_PASSWORD" | chpasswd 2>/dev/null || true
fi

# Start SSH server in background if needed
if command -v sshd >/dev/null 2>&1; then
    mkdir -p /var/run/sshd
    /usr/sbin/sshd -d -e &
fi

# Start Python native terminal server directly on $PORT (no Nginx/ttyd needed, guarantees 100% uptime and health check pass)
echo ">> Starting Python Native Web Terminal & Health Server on port $PORT..."
python3 server.py
