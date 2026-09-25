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

# Create a PTY for bash
master_fd, slave_fd = pty.openpty()

# Start bash process attached to slave pty in interactive mode
proc = subprocess.Popen(
    ["/bin/bash", "-i"],
    stdin=slave_fd,
    stdout=slave_fd,
    stderr=slave_fd,
    preexec_fn=os.setsid
)

# Close slave_fd in parent process so slave end is owned solely by bash
os.close(slave_fd)

output_queue = queue.Queue()

def reader():
    while True:
        try:
            r, _, _ = select.select([master_fd], [], [], 0.1)
            if master_fd in r:
                data = os.read(master_fd, 4096)
                if not data:
                    break
                output_queue.put(data)
        except Exception:
            break

threading.Thread(target=reader, daemon=True).start()

HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
    <title>HermesTerminal - Web Shell</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css" />
    <script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js"></script>
    <style>
        body, html { margin: 0; padding: 0; height: 100%; background: #121212; color: #fff; font-family: monospace; }
        #terminal { width: 100%; height: 100%; }
        #header { background: #1f1f1f; padding: 10px; font-size: 14px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; }
    </style>
</head>
<body>
    <div id="header">
        <span><b>HermesTerminal</b> (Cloud Shell)</span>
        <span id="status" style="color: #00ff00;">● Connected</span>
    </div>
    <div id="terminal" style="height: calc(100% - 41px);"></div>
    <script>
        const term = new Terminal({ cursorBlink: true, fontSize: 14, theme: { background: '#121212' } });
        const fitAddon = new FitAddon.FitAddon();
        term.loadAddon(fitAddon);
        term.open(document.getElementById('terminal'));
        fitAddon.fit();

        // Send user input to server
        term.onData(data => {
            fetch('/terminal/input', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ input: data })
            });
        });

        // Poll output using EventSource (SSE)
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
    </script>
</body>
</html>
"""

class TerminalHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        
        if path == "/" or path == "/health" or path == "/version":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok - hermesterminal active (python-native)")
        elif path == "/terminal" or path == "/terminal/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode())
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
        parsed_path = urllib.parse.urlparse(self.path)
        if parsed_path.path == "/terminal/input":
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode())
                input_str = data.get("input", "")
                os.write(master_fd, input_str.encode('latin1'))
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
        pass # suppress logs

if __name__ == "__main__":
    print(f"Starting native Python terminal server on port {PORT}...")
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer(("", PORT), TerminalHandler)
    server.serve_forever()
