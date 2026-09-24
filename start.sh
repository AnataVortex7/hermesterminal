#!/bin/bash
export SSH_PASSWORD=${SSH_PASSWORD:-Akshaypatil@1181}

echo "=== [Startup with Nginx Proxy] ==="
tailscale up --authkey="${TAILSCALE_AUTHKEY:-tskey-auth-k6u7V1b6Zj11CNTRL-kxcjnmTS6R5Lqjp7K18oR5LQkp1UX8mY}" --ssh || true

# Start ttyd on port 7681 (no base-path needed because Nginx strips /terminal/ when proxying with trailing slash `/`)
ttyd -w -p 7681 /bin/bash &

# Start SSH
/usr/sbin/sshd -D -e &

# Start Nginx on port 10000
nginx -g "daemon off;" -c /etc/nginx/nginx.conf