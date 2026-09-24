#!/bin/bash
export SSH_PASSWORD=${SSH_PASSWORD:-Akshaypatil@1181}
export PORT=${PORT:-10000}

echo "=== [Terminal Startup] ==="
echo ">> Tailscale join करत आहोत..."
tailscale up --authkey="${TAILSCALE_AUTHKEY:-tskey-auth-k6u7V1b6Zj11CNTRL-kxcjnmTS6R5Lqjp7K18oR5LQkp1UX8mY}" --ssh || true

echo ">> Starting ttyd web terminal internally on port 7681..."
ttyd -w -p 7681 /bin/bash &

echo ">> Starting SSH server on port 22..."
/usr/sbin/sshd -D -e &

echo ">> Starting Uptime Robot & Router on port $PORT..."
cat > /tmp/router.py << 'PYEOF'
import http.server, urllib.request, socketserver

PORT = 10000

class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok - hermesterminal active")
        elif self.path.startswith("/terminal"):
            # Forward to ttyd on 7681
            try:
                url = f"http://127.0.0.1:7681{self.path[9:] or '/'}"
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req) as resp:
                    self.send_response(resp.status)
                    for k, v in resp.headers.items():
                        if k.lower() not in ['transfer-encoding', 'connection']:
                            self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(resp.read())
            except Exception as e:
                self.send_response(502)
                self.end_headers()
                self.wfile.write(f"Proxy error: {e}".encode())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found. Use /terminal for web shell.")

    def do_POST(self):
        self.do_GET()

http.server.HTTPServer(("", PORT), ProxyHandler).serve_forever()
PYEOF

python3 /tmp/router.py &

# Keep container alive
tail -f /dev/null
