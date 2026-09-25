#!/usr/bin/env python3
import urllib.request
import time
import sys

def test_terminal():
    try:
        req = urllib.request.urlopen("https://hermesterminal.onrender.com/terminal/", timeout=15)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Terminal check status: {req.status}")
        if req.status == 200:
            return True
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Terminal check failed: {e}")
    return False

if __name__ == "__main__":
    print("Starting automated supervisor loop (every 5 minutes)...")
    while True:
        success = test_terminal()
        if success:
            print("SUCCESS: Terminal is fully operational!")
        else:
            print("FAILURE: Terminal is returning errors. Investigating...")
        time.sleep(300) # 5 minutes