#!/bin/bash
set -e

export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
export TZ="Asia/Kolkata"

PORT="${PORT:-10000}"

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
echo ">> Starting SSH server on port 22..."
/usr/sbin/sshd
echo ">> SSH running."

# 3. Single port server:
#    GET /        → 200 ok  (uptime robot / health check)
#    GET /health  → 200 ok  (same)
#    WS  /ssh     → WebSocket → SSH port 22 tunnel
cat > /tmp/router.py << 'PYEOF'
"""
Single port router:
  GET /         → 200 ok  (uptime robot health check)
  GET /health   → 200 ok
  WS  /ssh      → WebSocket tunnel → SSH port 22
"""
import asyncio
import os
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import hashlib, base64, struct

PORT = int(os.environ.get("PORT", 10000))
SSH_HOST = "127.0.0.1"
SSH_PORT = 22

# WebSocket handshake
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

def ws_accept_key(key):
    combined = key + WS_MAGIC
    sha1 = hashlib.sha1(combined.encode()).digest()
    return base64.b64encode(sha1).decode()

def forward(src, dst):
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except:
        pass
    finally:
        try: src.close()
        except: pass
        try: dst.close()
        except: pass

def ws_forward(client_sock):
    """WebSocket framing unwrap → SSH forward"""
    ssh_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ssh_sock.connect((SSH_HOST, SSH_PORT))

    def ws_to_ssh():
        try:
            while True:
                # WebSocket frame header
                header = b""
                while len(header) < 2:
                    chunk = client_sock.recv(2 - len(header))
                    if not chunk:
                        return
                    header += chunk

                fin = (header[0] & 0x80) != 0
                opcode = header[0] & 0x0f
                masked = (header[1] & 0x80) != 0
                payload_len = header[1] & 0x7f

                if opcode == 8:  # close
                    return

                if payload_len == 126:
                    ext = client_sock.recv(2)
                    payload_len = struct.unpack(">H", ext)[0]
                elif payload_len == 127:
                    ext = client_sock.recv(8)
                    payload_len = struct.unpack(">Q", ext)[0]

                mask_key = b""
                if masked:
                    mask_key = client_sock.recv(4)

                payload = b""
                while len(payload) < payload_len:
                    chunk = client_sock.recv(payload_len - len(payload))
                    if not chunk:
                        return
                    payload += chunk

                if masked:
                    payload = bytes(
                        b ^ mask_key[i % 4]
                        for i, b in enumerate(payload)
                    )

                if payload:
                    ssh_sock.sendall(payload)
        except:
            pass
        finally:
            try: ssh_sock.close()
            except: pass

    def ssh_to_ws():
        try:
            while True:
                data = ssh_sock.recv(4096)
                if not data:
                    break
                # WebSocket frame wrap (binary, unmasked)
                frame = bytes([0x82])  # FIN + binary
                length = len(data)
                if length < 126:
                    frame += bytes([length])
                elif length < 65536:
                    frame += bytes([126]) + struct.pack(">H", length)
                else:
                    frame += bytes([127]) + struct.pack(">Q", length)
                frame += data
                client_sock.sendall(frame)
        except:
            pass
        finally:
            try: client_sock.close()
            except: pass

    t1 = threading.Thread(target=ws_to_ssh, daemon=True)
    t2 = threading.Thread(target=ssh_to_ws, daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

class Router(BaseHTTPRequestHandler):
    def do_GET(self):
        # WebSocket upgrade request → /ssh
        upgrade = self.headers.get("Upgrade", "").lower()
        if upgrade == "websocket" and self.path == "/ssh":
            self._handle_ws_upgrade()
            return

        # Normal HTTP - health check
        if self.path in ("/", "/health"):
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_ws_upgrade(self):
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = ws_accept_key(key)

        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()

        # Raw socket वर SSH forward
        ws_forward(self.connection)

    def log_message(self, *args):
        pass

class ThreadedHTTPServer(HTTPServer):
    """प्रत्येक connection साठी नवीन thread"""
    def process_request(self, request, client_address):
        t = threading.Thread(
            target=self._new_request_thread,
            args=(request, client_address),
            daemon=True
        )
        t.start()

    def _new_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            pass
        finally:
            self.shutdown_request(request)

print(f">> Router on port {PORT}", flush=True)
print(f"   GET /       → 200 ok (health)", flush=True)
print(f"   GET /health → 200 ok (health)", flush=True)
print(f"   WS  /ssh    → SSH tunnel → port 22", flush=True)
ThreadedHTTPServer(("0.0.0.0", PORT), Router).serve_forever()
PYEOF

python3 /tmp/router.py &
ROUTER_PID=$!

echo "=== Tool Runner Ready ==="
echo "    Port $PORT:"
echo "    GET /health → ok  (uptime robot ला हे hit करा)"
echo "    WS  /ssh    → SSH tunnel"

cleanup() {
    echo ">> Shutting down..."
    kill $ROUTER_PID 2>/dev/null || true
    pkill sshd 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

wait $ROUTER_PID
