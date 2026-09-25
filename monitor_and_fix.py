#!/usr/bin/env python3
import urllib.request
import time

def check_terminal():
    try:
        req = urllib.request.urlopen("https://hermesterminal.onrender.com/", timeout=10)
        content = req.read().decode()
        print(f"Root response: {req.status}, content: {content[:50]}")
        return True
    except Exception as e:
        print(f"Error checking terminal: {e}")
        return False

if __name__ == "__main__":
    while True:
        check_terminal()
        time.sleep(180)