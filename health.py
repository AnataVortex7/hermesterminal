"""Health endpoint + built-in keep-alive self-ping. Exposes NO shell.
Self-ping: hit own public URL, wait max 3s for reply, sleep 5s, repeat.
URL comes from RENDER_EXTERNAL_URL (Render sets it automatically) or PUBLIC_URL."""
import os, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")
    do_HEAD = do_GET
    def log_message(self, *a):
        pass

def keepalive():
    url = os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        print("keepalive: no PUBLIC_URL / RENDER_EXTERNAL_URL set, self-ping disabled", flush=True)
        return
    url = url.rstrip("/") + "/health"
    while True:
        try:
            urllib.request.urlopen(url, timeout=3).read()
        except Exception:
            pass
        time.sleep(5)

threading.Thread(target=keepalive, daemon=True).start()
HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), H).serve_forever()
