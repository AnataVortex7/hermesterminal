import subprocess
import time
import requests
import json
import threading

print("Starting native terminal server test...")

# Start server and capture output
proc = subprocess.Popen(["python3", "server.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

# Robust retry loop to wait for server to start
server_ready = False
for attempt in range(10):
    try:
        resp = requests.get("http://localhost:10000/health", timeout=1)
        if resp.status_code == 200:
            server_ready = True
            print(f"Server ready after {attempt} attempts!")
            break
    except requests.exceptions.ConnectionError:
        time.sleep(1)

if not server_ready:
    print("❌ Error: Server failed to start in 10 seconds.")
    # Read output
    stdout, stderr = proc.communicate()
    print("STDOUT:", stdout)
    print("STDERR:", stderr)
    proc.terminate()
    proc.wait()
    exit(1)

output_received = []
stream_connected = threading.Event()

def read_stream():
    try:
        resp = requests.get("http://localhost:10000/terminal/stream", stream=True)
        if resp.status_code == 200:
            stream_connected.set()
        for line in resp.iter_lines(chunk_size=1):
            if line:
                decoded = line.decode('utf-8')
                if decoded.startswith("data: "):
                    data_json = decoded[6:]
                    data_str = json.loads(data_json)
                    output_received.append(data_str)
    except Exception as e:
        print("Stream exception:", e)

t = threading.Thread(target=read_stream, daemon=True)
t.start()

try:
    # 1. Test GET /terminal/
    resp = requests.get("http://localhost:10000/terminal/")
    print("GET /terminal/ status:", resp.status_code)
    assert resp.status_code == 200
    assert "HermesTerminal" in resp.text
    print("✅ Terminal UI page verified successfully.")

    # Wait for stream connection
    stream_connected.wait(timeout=5)
    time.sleep(1)

    # 2. Test POST /terminal/input with a test command
    cmd_payload = {"input": "echo 'HERMES_CMD_TEST_SUCCESS'\n"}
    post_resp = requests.post("http://localhost:10000/terminal/input", json=cmd_payload)
    print("POST /terminal/input status:", post_resp.status_code)
    assert post_resp.status_code == 200

    # 3. Wait and check output stream
    time.sleep(2)
    full_output = "".join(output_received)
    print("Captured stream output:")
    print(repr(full_output))

    assert "HERMES_CMD_TEST_SUCCESS" in full_output, "Command output not found in stream!"
    print("🎉 SUCCESS: Terminal executed python/bash command and returned output!")

finally:
    proc.terminate()
    proc.wait()
