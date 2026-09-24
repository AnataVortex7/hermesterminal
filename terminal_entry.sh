#!/bin/bash
export TERM=xterm-256color
export SHELL=/bin/bash
export HOME=/root
cd /root

# If tmux is installed, attach or create a persistent 'hermes' session
if command -v tmux >/dev/null 2>&1; then
    exec tmux new-session -A -s hermes /bin/bash
else
    exec /bin/bash
fi
