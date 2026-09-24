import http.server
import socketserver
import urllib.request
import os
import threading
import subprocess
import time

PORT = int(os.environ.get("PORT", 10000))
TTYD_PORT = 7681

def start_ttyd():
    # Ensure ttyd is installed
    if not os.path.exists("/usr/local/bin/ttyd"):
        os.system("curl -sL https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64 -o /usr/local/bin/ttyd && chmod +x /usr/local/bin/ttyd")
    
    # Run ttyd with base-path /terminal
    cmd = f"/usr/local/bin/ttyd -w -p {TTYD_PORT} --base-path /terminal /bin/bash"
    print(f"Starting ttyd: {cmd}")
    subprocess.Popen(cmd, shell=True)

class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok - hermesterminal active")
        elif self.path.startswith("/terminal"):
            # Proxy to ttyd
            try:
                # If path is just /terminal, redirect to /terminal/
                if self.path == "/terminal":
                    self.send_response(302)
                    self.send_header("Location", "/terminal/")
                    self.end_headers()
                    return

                url = f"http://127.0.0.1:{TTYD_PORT}{self.path}"
                req = urllib.request.Request(url, headers=dict(self.headers))
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
            self.wfile.write(b"Not Found")

    def do_POST(self):
        self.do_GET()

if __name__ == "__main__":
    threading.Thread(target=start_ttyd, daemon=True).start()
    time.sleep(2)
    print(f"Starting Python HTTP Proxy Router on port {PORT}...")
    server = socketserver.TCPServer(("", PORT), ProxyHandler)
    server.serve_forever()