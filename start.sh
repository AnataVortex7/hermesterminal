#!/bin/bash
echo "=== [Final Retry: Gotty with explicit address] ==="
/usr/local/bin/gotty -w -p 10000 --permit-write /bin/bash &
/usr/sbin/sshd -D -e