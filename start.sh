#!/bin/bash
set -e

export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
export TZ="Asia/Kolkata"

echo "=== [Tool Runner - SSH Server Startup] ==="

# 1. SSH Public Key env var मधून setup करा
if [ -n "$SSH_PUBLIC_KEY" ]; then
    echo ">> Setting up SSH public key..."
    mkdir -p /root/.ssh
    echo "$SSH_PUBLIC_KEY" > /root/.ssh/authorized_keys
    chmod 700 /root/.ssh
    chmod 600 /root/.ssh/authorized_keys
    echo ">> SSH key installed."
else
    echo "!! WARNING: SSH_PUBLIC_KEY env var not set - SSH login will fail!"
fi

# 2. SSH server start
echo ">> Starting SSH server..."
service ssh start || /usr/sbin/sshd

# 3. Keep-alive HTTP server (Koyeb alive ठेवण्यासाठी)
if [ -f /app/keep_alive.py ]; then
    echo ">> Starting keep-alive server on port 9000..."
    python3 /app/keep_alive.py &
fi

# 4. Nginx start - SSH TCP proxy + /health HTTP route
echo ">> Starting nginx (SSH proxy on :10000, health on :8080)..."
nginx -g 'daemon off;' &
NGINX_PID=$!

echo "=== Tool Runner Ready ==="
echo "    SSH available on port 10000"
echo "    Health check on port 8080/health"

# Graceful shutdown
cleanup() {
    echo ">> Shutting down..."
    kill $NGINX_PID 2>/dev/null || true
    service ssh stop 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

wait $NGINX_PID
