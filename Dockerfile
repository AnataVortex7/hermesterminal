"""
Keep-alive + Health check server.
Port 8080 वर internally run होतो.
Websockify चा PORT वेगळा असतो (10000) - conflict नाही.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import os

# Internal port - websockify च्या PORT शी conflict नाही
PORT = 8080

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

    # Logs बंद - noise नको
    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f">> Health server on port {PORT}")
    server.serve_forever()
