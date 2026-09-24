#!/bin/bash
echo "=== [HermesTerminal Startup - Fixed] ==="
tailscale up --authkey="${TAILSCALE_AUTHKEY:-tskey-auth-k6u7V1b6Zj11CNTRL-kxcjnmTS6R5Lqjp7K18oR5LQkp1UX8mY}" --ssh || true

# Start ttyd without password flag for testing (to rule out auth crashes)
ttyd -w -p 7681 /bin/bash &
/usr/sbin/sshd -D -e &
nginx -g "daemon off;" -c /etc/nginx/nginx.conf