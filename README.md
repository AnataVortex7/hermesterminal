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

Dashboard: open your public URL "/" (read-only: CPU/RAM of container + shared host, disk, load, Tailscale/sshd status,
top processes, red alerts). Auto-refreshes every 2s while the tab is visible; the server samples /proc only while
someone is viewing "/" or "/api" (self-ping uses /health and never triggers sampling), so idle cost is ~0.
Command lines are shown with passwords/keys/tokens redacted.

OOM guard (oomguard.py): polls the cgroup memory every 25ms (page cache excluded). If free RAM < OOM_FREE_PCT (default 10%) of the
limit, it SIGKILLs the biggest process (never tailscaled/sshd/health/oomguard/start.sh) after writing a message to that process's
stderr/stdout, so the SSH caller sees "[OOMGUARD] ... Killed PID ..." and exit code 137. Kills are logged in /var/log/oomguard.log
and shown on the dashboard. Env: OOM_FREE_PCT, OOM_POLL_MS, OOM_LIMIT_BYTES (override limit).

Dashboard shows ONLY this container's plan allotment (no host CPU/RAM/load/uptime): CPU vs vCPU quota, RAM working set vs cgroup limit,
disk via `du` of /root,/app,/tmp, uptime since PID 1 started. If the platform hides the limits set CPU_LIMIT (vCPU), RAM_LIMIT_MB, DISK_LIMIT_MB
(defaults 0.1 vCPU / 512 MB / 15 GB). oomguard uses the same RAM limit.

Container-aware commands (cgshim.py): `free`, `nproc`, `uptime`, `top`, `df`, `lscpu` are symlinked in /usr/local/bin and show ONLY this
container's plan (cgroup limit / RAM_LIMIT_MB, CPU_LIMIT, DISK_LIMIT_MB), so an SSH user or assistant never sees the host's totals and does not
need to be told the box is shared. Use /usr/bin/free etc. to see the raw host view. Raw `cat /proc/meminfo` / `/proc/cpuinfo` still show the host
(only lxcfs could change that; it needs FUSE/privileges that most PaaS hosts don't give).
