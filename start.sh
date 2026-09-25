#!/bin/bash
export SSH_PASSWORD=${SSH_PASSWORD:-Akshaypatil@1181}

echo "=== [HermesTerminal Startup] ==="

# Set root password if needed
echo "root:${SSH_PASSWORD}" | chpasswd 2>/dev/null || true

# Start Tailscale daemon in userspace mode (for unprivileged containers)
if command -v tailscaled >/dev/null 2>&1; then
    echo ">> Starting Tailscaled..."
    mkdir -p /var/run/tailscale /var/cache/tailscale /var/lib/tailscale
    tailscaled --tun=userspace-networking --statedir=/var/lib/tailscale &
    sleep 2
    if [ -n "$TAILSCALE_AUTHKEY" ]; then
        tailscale up --authkey="${TAILSCALE_AUTHKEY}" --ssh --hostname=hermesterminal || true
    else
        tailscale up --authkey="tskey-auth-k6u7V1b6Zj11CNTRL-kxcjnmTS6R5Lqjp7K18oR5LQkp1UX8mY" --ssh --hostname=hermesterminal || true
    fi
fi

# Ensure terminal entry script is executable
chmod +x /app/terminal_entry.sh

# Start ttyd on port 7681 with full bash and tmux support
echo ">> Starting ttyd web terminal on port 7681..."
ttyd -w -p 7681 /bin/bash /app/terminal_entry.sh &

# Start SSH
echo ">> Starting SSH server on port 22..."
/usr/sbin/sshd -D -e &

# Start Nginx on port 10000
echo ">> Starting Nginx reverse proxy on port 10000..."
nginx -g "daemon off;" -c /etc/nginx/nginx.conf
