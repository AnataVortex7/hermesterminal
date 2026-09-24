#!/bin/bash
export SSH_PASSWORD=${SSH_PASSWORD:-Akshaypatil@1181}
export PORT=${PORT:-10000}

echo "=== [Ultimate Fix: Base Path] ==="
tailscale up --authkey="${TAILSCALE_AUTHKEY:-tskey-auth-k6u7V1b6Zj11CNTRL-kxcjnmTS6R5Lqjp7K18oR5LQkp1UX8mY}" --ssh || true

# Bind ttyd to $PORT, and set base-path to /terminal
/usr/local/bin/ttyd -w -p $PORT --base-path /terminal /bin/bash &
/usr/sbin/sshd -D -e
