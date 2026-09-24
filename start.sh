#!/bin/bash
export SSH_PASSWORD=${SSH_PASSWORD:-Akshaypatil@1181}
export PORT=${PORT:-10000}

echo "=== [HermesTerminal Python Native Startup] ==="
tailscale up --authkey="${TAILSCALE_AUTHKEY:-tskey-auth-k6u7V1b6Zj11CNTRL-kxcjnmTS6R5Lqjp7K18oR5LQkp1UX8mY}" --ssh || true

echo ">> Starting SSH server on port 22..."
/usr/sbin/sshd -D -e &

echo ">> Starting Python Native Web Terminal on port $PORT..."
exec python3 /app/server.py