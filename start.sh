#!/bin/bash
echo "=== [HermesTerminal Startup - Ultimate Fix] ==="
tailscale up --authkey="${TAILSCALE_AUTHKEY:-tskey-auth-k6u7V1b6Zj11CNTRL-kxcjnmTS6R5Lqjp7K18oR5LQkp1UX8mY}" --ssh || true

# Run gotty directly on 10000
# -w allows writing, -p 10000 sets port
/usr/local/bin/gotty -w -p 10000 /bin/bash &
/usr/sbin/sshd -D -e