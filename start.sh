#!/bin/bash
export SSH_PASSWORD=${SSH_PASSWORD:-Akshaypatil@1181}
export PORT=${PORT:-10000}

echo "=== [Ultimate Fix: Binding to $PORT] ==="
tailscale up --authkey="${TAILSCALE_AUTHKEY:-tskey-auth-k6u7V1b6Zj11CNTRL-kxcjnmTS6R5Lqjp7K18oR5LQkp1UX8mY}" --ssh || true

# Start ttyd binding to the $PORT env var
# Using --port $PORT to be explicit
ttyd -w -p $PORT /bin/bash &
/usr/sbin/sshd -D -e
