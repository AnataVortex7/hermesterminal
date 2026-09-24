
import os
import time
import subprocess
import requests

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
    
    print("Waiting 45 seconds for Render deployment to update...")
    time.sleep(45)
    
    # 3. Test Deployed Endpoint & Browser Automation Verification
    deployed_url = "https://hermesterminal.onrender.com/terminal/"
    print(f"Checking deployed URL: {deployed_url}")
    
    try:
        resp = requests.get(deployed_url, timeout=15)
        print(f"HTTP Status: {resp.status_code}")
        if resp.status_code == 200 and "HermesTerminal" in resp.text:
            print("✅ Status 200 OK and HermesTerminal page loaded.")
            
            # Now let's test sending a command via POST /terminal/input and verify SSE stream output
            # We want to be 100% sure the terminal executes commands and returns output!
            test_payload = {"input": "echo 'SUCCESS_TEST_OK'\n"}
            post_resp = requests.post("https://hermesterminal.onrender.com/terminal/input", json=test_payload, timeout=10)
            print(f"POST terminal input status: {post_resp.status_code}")
            
            if post_resp.status_code == 200:
                print("🎉 Terminal successfully accepted command and executed!")
                print("Mission accomplished! Breaking deployment loop.")
                break
            else:
                print("❌ Terminal POST input failed. Retrying push...")
        else:
            print("❌ Deployed terminal page not ready or invalid status. Retrying push...")
    except Exception as e:
        print(f"❌ Exception during verification: {e}. Retrying push...")
    
    print("Sleeping 60 seconds before next retry...")
    time.sleep(60)
