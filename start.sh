#!/bin/bash
export SSH_PASSWORD=${SSH_PASSWORD:-Akshaypatil@1181}
export PORT=${PORT:-10000}

echo "=== [Terminal Startup] ==="
echo ">> Tailscale join करत आहोत..."
tailscale up --authkey="${TAILSCALE_AUTHKEY:-tskey-auth-k6u7V1b6Zj11CNTRL-kxcjnmTS6R5Lqjp7K18oR5LQkp1UX8mY}" --ssh || true

echo ">> Starting ttyd web terminal on port $PORT..."
ttyd -w -p $PORT -c root:$SSH_PASSWORD /bin/bash &

echo ">> Starting SSH server on port 22..."
/usr/sbin/sshd -D -e &

# Keep container alive
tail -f /dev/null
