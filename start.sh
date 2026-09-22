#!/bin/bash
set -e

export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
export TZ="Asia/Kolkata"

# Render PORT - HTTP साठी
PORT="${PORT:-10000}"
# SSH internal port - वेगळा
SSH_PROXY_PORT=2222

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

# 2. SSH server - internal port 22
echo ">> Starting SSH server..."
/usr/sbin/sshd
echo ">> SSH running on port 22."

# 3. HTTP server - Render ला हे दिसते
#    /health → 200 ok  (Render health check)
#    /ssh-info → SSH proxy port info
cat << 'PYEOF' > /tmp/http_server.py
"""
HTTP server - Render ला हे दिसते
Port: PORT env var (10000)

Routes:
  GET /health    → 200 ok  (Render health check, zero load)
  GET /          → 200 (generic)
  बाकी           → 404
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import os, sys

PORT = int(os.environ.get("PORT", 10000))
SSH_PROXY_PORT = int(os.environ.get("SSH_PROXY_PORT", 2222))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/health", "/"):
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Logs बंद - noise नको

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f">> HTTP server on port {PORT}", flush=True)
    print(f"   /health → 200 ok", flush=True)
    server.serve_forever()
PYEOF

echo ">> Starting HTTP server on port $PORT (Render health check)..."
python3 /tmp/http_server.py &
HTTP_PID=$!

# 4. SSH proxy - वेगळ्या port वर (Render internal network)
#    Server 1 हा port use करतो SSH connect साठी
echo ">> Starting SSH proxy on internal port $SSH_PROXY_PORT..."
socat TCP-LISTEN:${SSH_PROXY_PORT},fork,reuseaddr TCP:127.0.0.1:22 &
SOCAT_PID=$!

echo "=== Tool Runner Ready ==="
echo "    HTTP (Render public): port $PORT"
echo "    SSH proxy (internal): port $SSH_PROXY_PORT → 22"
echo "    Health: https://your-service.onrender.com/health"

# Graceful shutdown
cleanup() {
    echo ">> Shutting down..."
    kill $HTTP_PID 2>/dev/null || true
    kill $SOCAT_PID 2>/dev/null || true
    /usr/sbin/sshd -T 2>/dev/null || true
    pkill sshd 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

wait $HTTP_PID
