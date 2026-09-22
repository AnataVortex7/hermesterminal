#!/bin/bash
set -e

export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
export TZ="Asia/Kolkata"

# Render PORT env var - हाच एकमेव public port
PORT="${PORT:-10000}"

echo "=== [Tool Runner Startup] ==="
echo "    Single public port: $PORT"

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
echo ">> Starting SSH server on internal port 22..."
service ssh start || /usr/sbin/sshd
echo ">> SSH running."

# 3. Single-port router - Python script
#    /health → 200 ok  (Render health check)
#    बाकी सगळं → SSH port 22 ला forward
cat <<'PYEOF' > /tmp/port_router.py
"""
Single port router:
- HTTP GET /health → 200 ok (Render health check, no load)
- बाकी सगळे connections → SSH port 22 forward

Render ला एकच port दिसतो - PORT env var
"""
import socket
import threading
import os
import sys

PORT = int(os.environ.get("PORT", 10000))
SSH_PORT = 22
HEALTH_RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"

def is_http_health(data):
    """HTTP GET /health request check"""
    try:
        return data.startswith(b"GET /health") or data.startswith(b"GET / ")
    except:
        return False

def forward(src, dst):
    """Data forward between two sockets"""
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

def handle_client(client_sock, client_addr):
    try:
        # पहिले data peek करा - HTTP की SSH?
        client_sock.settimeout(5)
        try:
            first_data = client_sock.recv(1024, socket.MSG_PEEK)
        except:
            client_sock.close()
            return
        client_sock.settimeout(None)

        if is_http_health(first_data):
            # HTTP /health request - simple 200 response
            client_sock.recv(1024)  # buffer clear
            client_sock.sendall(HEALTH_RESPONSE)
            client_sock.close()
            return

        # SSH connection - port 22 ला forward
        ssh_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ssh_sock.connect(("127.0.0.1", SSH_PORT))

        # Bidirectional forward - दोन threads
        t1 = threading.Thread(target=forward, args=(client_sock, ssh_sock), daemon=True)
        t2 = threading.Thread(target=forward, args=(ssh_sock, client_sock), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    except Exception as e:
        try: client_sock.close()
        except: pass

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", PORT))
    server.listen(100)
    print(f">> Port router listening on {PORT}", flush=True)
    print(f"   /health → 200 ok", flush=True)
    print(f"   SSH     → 127.0.0.1:22", flush=True)

    while True:
        try:
            client_sock, client_addr = server.accept()
            t = threading.Thread(
                target=handle_client,
                args=(client_sock, client_addr),
                daemon=True
            )
            t.start()
        except Exception as e:
            print(f"!! Accept error: {e}", flush=True)

if __name__ == "__main__":
    main()
PYEOF

echo ">> Starting single-port router on port $PORT..."
python3 /tmp/port_router.py &
ROUTER_PID=$!

echo "=== Tool Runner Ready ==="
echo "    Public port $PORT:"
echo "    - GET /health → 200 ok"
echo "    - SSH connections → port 22"

# Graceful shutdown
cleanup() {
    echo ">> Shutting down..."
    kill $ROUTER_PID 2>/dev/null || true
    service ssh stop 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

wait $ROUTER_PID
