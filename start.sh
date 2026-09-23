#!/bin/bash
set -e

export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
export TZ="Asia/Kolkata"

PORT="${PORT:-10000}"

echo "=== [Tool Runner Startup] ==="

# 0. Tailscale (optional but recommended) - ek TAILSCALE_AUTHKEY ने ha container
#    tumchya tailnet madhe join hoto. Mag PC ani phone (Tailscale app madhe
#    tyach account ने login) थेट ह्या container च्या tailscale IP वर SSH करू
#    शकतात - koणतीही public key copy-paste karaychi गरज nahi, ani connection
#    public internet var expose pan hot nahi (jast secure).
if [ -n "$TAILSCALE_AUTHKEY" ]; then
    echo ">> Tailscale join karत आहोत..."
    mkdir -p /var/run/tailscale /var/lib/tailscale
    # Bahutek free/PaaS cloud vars (Render, Railway, etc.) NET_ADMIN / /dev/net/tun
    # देत nahit, म्हणून userspace-networking mode default ठेवली (root/tun शिवाय चालते).
    tailscaled --state=/var/lib/tailscale/tailscaled.state \
        --tun=userspace-networking \
        --socks5-server=localhost:1055 > /var/log/tailscaled.log 2>&1 &
    sleep 2
    tailscale up \
        --authkey="$TAILSCALE_AUTHKEY" \
        --hostname="${TAILSCALE_HOSTNAME:-hermes-cloud}" \
        --accept-routes=false \
        --ssh=false || echo "!! Tailscale up fail झालं, log पहा: /var/log/tailscaled.log"
    TS_IP=$(tailscale ip -4 2>/dev/null || echo "??")
    echo ">> Tailscale IP: $TS_IP   (PC/Termux वरून: ssh root@$TS_IP)"
else
    echo ">> TAILSCALE_AUTHKEY set nahi -> फक्त websocket/SSH-key pathane connect करता येईल."
fi

# 1. SSH Public Key setup
# SSH_PUBLIC_KEY      -> normal PC/browser client chi key
# TERMUX_PUBLIC_KEY   -> Termux (phone / another cloud) varun generate keleli key
# donhi asतील tar donhi authorized_keys madhe jातात, kontihi ek client connect karu shakते.
mkdir -p /root/.ssh
chmod 700 /root/.ssh
: > /root/.ssh/authorized_keys

KEY_COUNT=0
if [ -n "$SSH_PUBLIC_KEY" ]; then
    echo ">> Installing SSH_PUBLIC_KEY (PC/browser client)..."
    echo "$SSH_PUBLIC_KEY" >> /root/.ssh/authorized_keys
    KEY_COUNT=$((KEY_COUNT+1))
fi

if [ -n "$TERMUX_PUBLIC_KEY" ]; then
    echo ">> Installing TERMUX_PUBLIC_KEY (Termux client)..."
    echo "$TERMUX_PUBLIC_KEY" >> /root/.ssh/authorized_keys
    KEY_COUNT=$((KEY_COUNT+1))
fi

if [ "$KEY_COUNT" -gt 0 ]; then
    chmod 600 /root/.ssh/authorized_keys
    echo ">> $KEY_COUNT SSH key(s) ready."
else
    echo "!! WARNING: SSH_PUBLIC_KEY / TERMUX_PUBLIC_KEY konतीच set nahi!"
fi

# 1b. Optional SIMPLE password login (उदा. SSH_PASSWORD="Akshaymeratpatil@1181")
#     Key hi lambच rahते (ती crypto ने banते, custom shortcut nasतो),
#     पण key ऐवजी/सोबत साधा password वापरायचा असेल तर हा env var सेट करा.
if [ -n "$SSH_PASSWORD" ]; then
    echo ">> SSH_PASSWORD set -> password login pan enable karत आहोत (key + password donhi chalतील)."
    echo "root:$SSH_PASSWORD" | chpasswd
    sed -i 's/^PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config
else
    echo ">> SSH_PASSWORD set nahi -> फक्त key-based login चालू राहील (जास्त सुरक्षित)."
fi

# 1c. Persistent session: SSH disconnect zala tarihi चालू असलेलं command
#     चालूच rahते. Login shell madhe automatic tmux session attach/create
#     hote ("hermes" name ने). परत connect केलं की तीच session परत dिसते.
cat >> /root/.bashrc << 'BASHRC_EOF'

# Hermes Terminal: auto-attach persistent tmux session
if command -v tmux >/dev/null 2>&1 && [ -z "$TMUX" ] && [ -n "$SSH_CONNECTION" ]; then
    tmux attach -t hermes 2>/dev/null || tmux new -s hermes
fi
BASHRC_EOF

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

def ws_forward(client_sock, client_rfile):
    """WebSocket framing unwrap → SSH forward"""
    ssh_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ssh_sock.connect((SSH_HOST, SSH_PORT))

    def recv_exact(n):
        # client_rfile वरून वाचतो (buffered) -> handshake parsing वेळी
        # आधीच socket buffer मधे आलेला पहिला frame कधीच हरवत नाही.
        data = b""
        while len(data) < n:
            chunk = client_rfile.read(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def ws_to_ssh():
        try:
            while True:
                # WebSocket frame header
                header = recv_exact(2)
                if header is None:
                    return

                fin = (header[0] & 0x80) != 0
                opcode = header[0] & 0x0f
                masked = (header[1] & 0x80) != 0
                payload_len = header[1] & 0x7f

                if opcode == 8:  # close
                    return

                if payload_len == 126:
                    ext = recv_exact(2)
                    if ext is None:
                        return
                    payload_len = struct.unpack(">H", ext)[0]
                elif payload_len == 127:
                    ext = recv_exact(8)
                    if ext is None:
                        return
                    payload_len = struct.unpack(">Q", ext)[0]

                mask_key = b""
                if masked:
                    mask_key = recv_exact(4)
                    if mask_key is None:
                        return

                payload = b""
                if payload_len:
                    payload = recv_exact(payload_len)
                    if payload is None:
                        return

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

        # Raw socket वर SSH forward (rfile सुद्धा pass -> buffered bytes lost होत नाहीत)
        ws_forward(self.connection, self.rfile)

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
