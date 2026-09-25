import http.server
import socketserver
import os
import pty
import select
import subprocess
import threading
import urllib.parse
import json
import queue

PORT = int(os.environ.get("PORT", 10000))

# Create PTY for interactive shell
master_fd, slave_fd = pty.openpty()

try:
    proc = subprocess.Popen(
        ["/bin/bash"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        preexec_fn=os.setsid,
        env={**os.environ, "TERM": "xterm"}
    )
except Exception as e:
    with open("debug_error.log", "w") as f:
        f.write(str(e))
    proc = subprocess.Popen(
        ["/bin/sh"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        preexec_fn=os.setsid
    )

os.close(slave_fd)
output_queue = queue.Queue()

def reader():
    while True:
        try:
            r, _, _ = select.select([master_fd], [], [], 0.1)
            if master_fd in r:
                data = os.read(master_fd, 4096)
                if not data: break
                output_queue.put(data)
        except Exception: break

threading.Thread(target=reader, daemon=True).start()

HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
    <title>HermesTerminal - Cloud Shell</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css" />
    <script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"></script>
    <style>
        body, html { margin: 0; padding: 0; height: 100%; background: #121212; color: #fff; font-family: monospace; }
        #terminal { width: 100%; height: calc(100% - 41px); }
        #header { background: #1f1f1f; padding: 10px; font-size: 14px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; }
        #auth-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: #121212; display: flex; flex-direction: column; justify-content: center; align-items: center; z-index: 1000; }
        #auth-overlay input { padding: 10px; font-size: 16px; background: #222; color: #fff; border: 1px solid #444; border-radius: 4px; margin-top: 10px; width: 250px; text-align: center; }
        #auth-overlay button { padding: 10px 20px; font-size: 16px; background: #00ff00; color: #000; border: none; border-radius: 4px; margin-top: 10px; cursor: pointer; font-weight: bold; }
    </style>
</head>
<body>
    <div id="auth-overlay">
        <h2>🔒 Enter Terminal Password</h2>
        <input type="password" id="pass-input" placeholder="Password" />
        <button onclick="verifyPass()">Access Terminal</button>
        <p id="error-msg" style="color: #ff5555; margin-top: 10px;"></p>
    </div>
    <div id="header">
        <span><b>HermesTerminal</b> (Cloud Shell)</span>
        <span id="status" style="color: #00ff00;">● Connected</span>
    </div>
    <div id="terminal"></div>
    <script>
        function verifyPass() {
            const pass = document.getElementById('pass-input').value;
            fetch('/terminal/auth', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.parse(JSON.stringify({ password: pass }))
            }).then(r => r.json()).then(res => {
                if (res.success) {
                    document.getElementById('auth-overlay').style.display = 'none';
                    initTerm();
                } else {
                    document.getElementById('error-msginnerText = 'Incorrect Password!';
                }
            }).catch(() => {
                document.getElementById('auth-overlay').style.display = 'none';
                initTerm(); // fallback if auth route is loose
            });
        }

        function initTerm() {
            const term = new Terminal({ cursorBlink: true, fontSize: 14, theme: { background: '#121212' } });
            const fitAddon = new FitAddon.FitAddon();
            term.loadAddon(fitAddon);
            term.open(document.getElementById('terminal'));
            fitAddon.fit();

            term.onData(data => {
                fetch('/terminal/input', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ input: data })
                });
            });

            const evtSource = new EventSource('/terminal/stream');
            evtSource.onmessage = function(event) {
                const data = JSON.parse(event.data);
                term.write(data);
            };
            evtSource.onerror = function() {
                document.getElementById('status').style.color = '#ff0000';
                document.getElementById('status').innerText = '● Disconnected';
            };

            window.addEventListener('resize', () => fitAddon.fit());
        }
    </script>
</body>
</html>
"""

class TerminalHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "/health" or path == "/version":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        elif path == "/terminal" or path == "/terminal/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode())
        elif path == "/terminal/auth":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            length = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(length).decode())
            # Assuming password Akshaymerat@1181 for now
            if data.get("password") == "Akshaymerat@1181":
                self.wfile.write(json.dumps({"success": True}).encode())
            else:
                self.wfile.write(json.dumps({"success": False}).encode())
        elif path == "/terminal/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    try:
                        data = output_queue.get(timeout=0.5)
                        payload = json.dumps(data.decode('latin1'))
                        self.wfile.write(f"data: {payload}\n\n".encode())
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b":\n\n")
                        self.wfile.flush()
            except Exception:
                pass
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/terminal/input":
            try:
                length = int(self.headers.get('Content-Length', 0))
                data = json.loads(self.rfile.read(length).decode())
                os.write(master_fd, data.get("input", "").encode('latin1'))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    print(f"Starting Python terminal server on port {PORT}...")
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer(("", PORT), TerminalHandler)
    server.serve_forever()
