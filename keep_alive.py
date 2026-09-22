"""
Keep-alive + Health check server.
Port 9000 वर internally run होतो.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import os

PORT = 9000

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

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f">> Health server on port {PORT}")
    server.serve_forever()
