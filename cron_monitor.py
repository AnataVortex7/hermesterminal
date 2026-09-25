#!/usr/bin/env python3
import urllib.request
import urllib.error
import sys

URL = "https://hermesterminal.onrender.com/terminal/"

def check():
    try:
        req = urllib.request.urlopen(URL, timeout=10)
        print(f"STATUS: {req.status}")
        if req.status == 200:
            print("SUCCESS: Terminal is working!")
            sys.exit(0)
        else:
            print(f"WARNING: Unexpected status {req.status}")
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    check()
