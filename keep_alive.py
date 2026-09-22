"""
Lightweight keep-alive HTTP server for Koyeb free tier.
Port 9000 वर run होतो - Koyeb चा health check साठी.
/health hit केल्यावर फक्त 'ok' return करतो, कोणताही load नाही.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import os

PORT = int(os.environ.get("KEEPALIVE_PORT", 9000))

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    # Logs बंद - unnecessary output नको
    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f">> Keep-alive server running on port {PORT}")
    server.serve_forever()
