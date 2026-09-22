#!/bin/bash
set -e

export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
export TZ="Asia/Kolkata"

echo "=== [Tool Runner Startup] ==="

# 1. SSH Public Key setup
if [ -n "$SSH_PUBLIC_KEY" ]; then
    echo ">> Installing SSH public key..."
    mkdir -p /root/.ssh
    echo "$SSH_PUBLIC_KEY" > /root/.ssh/authorized_keys
    chmod 700 /root/.ssh
    chmod 600 /root/.ssh/authorized_keys
    echo ">> SSH key ready."
else
    echo "!! WARNING: SSH_PUBLIC_KEY not set!"
fi

# 2. SSH server start
echo ">> Starting SSH server..."
service ssh start || /usr/sbin/sshd
echo ">> SSH running on port 22."

# 3. Keep-alive health server (port 9000)
if [ -f /app/keep_alive.py ]; then
    echo ">> Starting keep-alive server on port 9000..."
    python3 /app/keep_alive.py &
    KEEPALIVE_PID=$!
fi

# 4. socat - SSH TCP proxy
#    port 10000 → internal SSH port 22
#    nginx stream module नाही लागत!
echo ">> Starting SSH TCP proxy on port 10000..."
socat TCP-LISTEN:10000,fork,reuseaddr TCP:127.0.0.1:22 &
SOCAT_PID=$!
echo ">> SSH proxy ready: port 10000 → port 22"

# 5. nginx - फक्त /health HTTP route (stream नाही)
echo ">> Starting nginx for /health route on port 8080..."
nginx -g 'daemon off;' &
NGINX_PID=$!

echo "=== Tool Runner Ready ==="
echo "    SSH:    port 10000 (via socat)"
echo "    Health: port 8080/health (via nginx)"

# Graceful shutdown
cleanup() {
    echo ">> Shutting down..."
    kill $SOCAT_PID 2>/dev/null || true
    kill $NGINX_PID 2>/dev/null || true
    kill $KEEPALIVE_PID 2>/dev/null || true
    service ssh stop 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

wait $SOCAT_PID
