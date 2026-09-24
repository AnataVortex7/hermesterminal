#!/bin/bash
export SSH_PASSWORD=${SSH_PASSWORD:-Akshaypatil@1181}

echo "=== [HermesTerminal Startup] ==="
echo ">> Tailscale join..."
tailscale up --authkey="${TAILSCALE_AUTHKEY:-tskey-auth-k6u7V1b6Zj11CNTRL-kxcjnmTS6R5Lqjp7K18oR5LQkp1UX8mY}" --ssh || true

echo ">> Starting ttyd web terminal on port 7681..."
ttyd -w -p 7681 -c root:$SSH_PASSWORD /bin/bash &

echo ">> Starting SSH server on port 22..."
/usr/sbin/sshd -D -e &

echo ">> Starting Nginx reverse proxy on port 10000..."
nginx -g "daemon off;" -c /etc/nginx/nginx.conf