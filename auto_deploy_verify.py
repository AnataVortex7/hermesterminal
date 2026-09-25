import os
import time
import subprocess
import requests
import json
import threading

def run_cmd(cmd):
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return res.returncode, res.stdout, res.stderr

print("Starting continuous push & browser verification loop...")

iteration = 0
while True:
    iteration += 1
    print(f"\n--- Iteration {iteration} ---")
    
    # 1. Update version.txt
    subprocess.run(["python3", "update_version.py"], check=True)
    
    # 2. Git add, commit, push
    run_cmd("git add .")
    code, out, err = run_cmd('git commit -m "Auto deployment update v' + time.strftime('%Y%m%d-%H%M%S') + '"')
    print("Git commit output:", out)
    
    code, out, err = run_cmd("git push origin main")
    print("Git push output:", out, err)
    
    print("Waiting 60 seconds for Render deployment to update...")
    time.sleep(60)
    
    # 3. Test Deployed Endpoint & Browser Automation Verification
    deployed_url = "https://hermesterminal.onrender.com/terminal/"
    print(f"Checking deployed URL: {deployed_url}")
    
    try:
        resp = requests.get(deployed_url, timeout=15)
        print(f"HTTP Status: {resp.status_code}")
        if resp.status_code == 200 and "HermesTerminal" in resp.text:
            print("✅ Status 200 OK and HermesTerminal page loaded.")
            
            # Start background thread to listen to the live SSE stream on the deployed server
            output_received = []
            stream_connected = threading.Event()
            
            def read_deployed_stream():
                try:
                    stream_url = "https://hermesterminal.onrender.com/terminal/stream"
                    stream_resp = requests.get(stream_url, stream=True, timeout=15)
                    if stream_resp.status_code == 200:
                        stream_connected.set()
                    for line in stream_resp.iter_lines(chunk_size=1):
                        if line:
                            decoded = line.decode('utf-8')
                            if decoded.startswith("data: "):
                                data_json = decoded[6:]
                                data_str = json.loads(data_json)
                                output_received.append(data_str)
                except Exception as stream_err:
                    print("Stream thread error:", stream_err)
            
            t = threading.Thread(target=read_deployed_stream, daemon=True)
            t.start()
            
            # Wait for stream connection
            stream_connected.wait(timeout=10)
            time.sleep(2)
            
            # Send test command to input
            test_payload = {"input": "echo 'SUCCESS_TEST_OK'\n"}
            post_resp = requests.post("https://hermesterminal.onrender.com/terminal/input", json=test_payload, timeout=10)
            print(f"POST terminal input status: {post_resp.status_code}")
            
            if post_resp.status_code == 200:
                time.sleep(4)  # Wait for command output to stream back
                full_output = "".join(output_received)
                print("Captured SSE stream output from deployed server:")
                print(repr(full_output))
                
                if "SUCCESS_TEST_OK" in full_output:
                    print("🎉 SUCCESS: Deployed terminal executed command and streamed output successfully!")
                    print("Mission accomplished! Breaking deployment loop.")
                    break
                else:
                    print("❌ Error: Command executed but output not found in SSE stream! Retrying push...")
            else:
                print("❌ Deployed terminal POST input failed. Retrying push...")
        else:
            print("❌ Deployed terminal page not ready or invalid status. Retrying push...")
    except Exception as e:
        print(f"❌ Exception during verification: {e}. Retrying push...")
    
    print("Sleeping 60 seconds before next retry...")
    time.sleep(60)
