# hermes-ts

Env vars on the host:
- TAILSCALE_AUTHKEY  (required; Reusable + Ephemeral key from Tailscale admin -> Keys)
- TAILSCALE_HOSTNAME (optional, default hermes-cloud)
- PUBLIC_URL         (optional; Render sets RENDER_EXTERNAL_URL itself, so usually not needed)

Connect from any device on the same Tailscale account, no password, no login:
    ssh root@hermes-cloud
    ssh root@hermes-cloud 'any command'          # for your assistant
    ssh -t root@hermes-cloud tmux new -A -s hermes

Security model: sshd listens on 127.0.0.1 only and is exposed via `tailscale serve`
solely inside your tailnet. Anyone/any device on your tailnet gets root, so only
add your own devices (don't share this node to others).
Self-ping (health.py): GET <public url>/health with 3s timeout, then sleep 5s, forever.
