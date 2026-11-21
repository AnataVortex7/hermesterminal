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

File manager API (fsd.py): runs inside health.py as one idle thread (no extra process), listens on 127.0.0.1:FS_PORT (default 8765) and is
published by `tailscale serve --tcp` ONLY to your tailnet, same as sshd. No password. Whole machine by default (FS_ROOT=/); set FS_ROOT to confine,
FS_ENABLED=0 to turn off. Endpoints: /fs/ping /fs/list /fs/read /fs/write /fs/op (see fsd.py header). Used by the tgstorage "Machines" page.
Mutating calls need header `X-Requested-With: fsd` (blocks malicious web pages from firing requests at your tailnet name); Host must be an IP / short name / *.ts.net.

Auto port proxy (health.py): any app that starts listening on a local TCP port is instantly reachable on the SAME public URL under a
path prefix, no config, no password, no restart:
    your app on 8097  ->  https://<public-url>/8097/
    your app on 4533  ->  https://<public-url>/4533/
    your app on 8977  ->  https://<public-url>/8977/
HTTP (all methods, streaming/SSE/large uploads+downloads) and WebSocket/Upgrade are supported. Ports are discovered live from
/proc/net/tcp, the dashboard "/" lists every open port as a clickable link. Redirects (Location) are rewritten to keep the /<port>/ prefix.
Apps that emit absolute paths (/static/app.js, /socket.io/): the request is routed by the Referer (/<port>/...) or by the `hp_port`
cookie set on your last /<port>/ visit, so most apps work without any base-path setting. (Apps that support a base path, e.g. --base-url /8097, are still the most robust.)
Never proxied (blocked): PORT (this server), 2222 (sshd), 1055 (tailscale socks), FS_PORT (file API). Extra: PROXY_BLOCK=3306,5432.
Headers sent to your app: Host=localhost:<port>, X-Forwarded-Host/Proto/For, X-Forwarded-Prefix=/<port>.
Password (optional): set env PROXY_PASSWORD=yourpass and EVERY /<port>/ route (HTTP, WebSocket, absolute-path fallback) asks for it.
  Browser: Basic-auth prompt (user name = anything, password = PROXY_PASSWORD), then a 'hp_auth' cookie keeps you logged in for all ports.
  Scripts/APIs: `curl -u x:yourpass https://<url>/8097/`  or  header `X-Proxy-Password: yourpass`.
  The credentials are stripped before the request reaches your app. /health stays open (self-ping) and the dashboard "/" stays open (read-only).
  PROXY_PASSWORD unset/empty = no password (anyone with the URL can open any of your ports; use PROXY_BLOCK for ports that must stay private).

Backup / restore (backup.py, optional - fully skipped unless BACKUP_REMOTE is set; every error is logged to /var/log/hermes-backup.log and skipped):
  Env: BACKUP_REMOTE=gdrive:hermes   (any rclone remote: Drive, S3/R2/B2, OneDrive, SFTP, ...)
       RCLONE_CONF_B64=<base64 -w0 ~/.config/rclone/rclone.conf>   (or native RCLONE_CONFIG_<NAME>_TYPE=... env vars, no file needed)
       optional: BACKUP_NAME (folder on the remote, default TAILSCALE_HOSTNAME), BACKUP_INTERVAL=300, BACKUP_PATHS="/root /home /opt /srv /usr/local /var/www",
                 BACKUP_EXCLUDES="*.log /root/big", BACKUP_ZSTD_LEVEL=3, BACKUP_APT=0, BACKUP_FORCE_RESTORE=1, BACKUP_ENABLED=0
  - Every 5 min: each folder directly under a backup path is a "unit". Only units that changed (path/mode/owner/size/mtime) are re-packed
    (tar + zstd, permissions/owners/symlinks kept) and uploaded by rclone; unchanged units are skipped, so it is fast.
  - Deleted over ssh -> deleted from the backup in the next cycle (a deleted folder's archive is removed from the remote, a deleted file is
    missing from the re-packed archive).
  - Restore: on a fresh box (no /var/lib/hermes-backup/restored marker) every unit is downloaded+extracted, then apt packages you installed
    (apt-mark showmanual minus the base image) are re-installed. pip/npm -g/cargo/manual installs live in /usr/local, /root, /opt and come back as files.
    Add other install locations to BACKUP_PATHS (e.g. add /usr/lib/node_modules). Do NOT add /etc, /usr, /var/lib/dpkg (they belong to the image).
  - Safety: backups start only after the restore finished. If the remote is unreachable at boot, nothing is backed up (retry every cycle) so a
    broken start can never overwrite a good backup with an empty box. A unit that failed to restore is never overwritten (3 tries, then skipped).
  - Dashboard "/" shows a Backup card (state, last ok, units uploaded). Logs: /var/log/hermes-backup.log
  - Running databases (postgres/mysql) are copied live = may be inconsistent; stop them or dump to a file inside a backed-up folder.
