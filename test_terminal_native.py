
import subprocess
import time
import requests
import json

proc = subprocess.Popen(["python3", "server.py"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(2)

try:
    # 1. Test GET /terminal/
    resp = requests.get("http://localhost:10000/terminal/")
    print("GET /terminal/ status:", resp.status_code)
    assert resp.status_code == 200
    assert "HermesTerminal" in resp.text
    print("Terminal UI page verified successfully.")

    # 2. Test POST /terminal/input with a test command like 'echo "HELLO_HERMES"'
    cmd_payload = {"input": "echo 'HELLO_HERMES'\n"}
    post_resp = requests.post("http://localhost:10000/terminal/input", json=cmd_payload)
    print("POST /terminal/input status:", post_resp.status_code)
    assert post_resp.status_code == 200

    # 3. Test reading stream (or check output queue via a short test)
    print("Terminal input test dispatched successfully.")

finally:
    proc.terminate()
    proc.wait()
