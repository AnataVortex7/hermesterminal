"""Single public server: /health -> 'ok' (self-ping), / -> read-only dashboard, /api -> JSON. No shell, no write endpoints.

Shows ONLY this container's plan allotment (cgroup) - never the host machine:
  CPU  = cgroup cpu.stat vs the vCPU quota        RAM = cgroup working set (page cache excluded) vs the cgroup limit
  Disk = `du` of /root,/app,/tmp vs DISK_LIMIT_MB  Uptime = since the container's PID 1 started
Limits that cannot be read from inside can be set: CPU_LIMIT (vCPU), RAM_LIMIT_MB, DISK_LIMIT_MB
(defaults when nothing is visible: 0.1 vCPU / 512 MB / 15 GB, same as the hermes repo).
Sampling runs only while somebody views "/" or "/api" (idle cost ~0); /health never wakes it."""
import os, re, json, time, shutil, threading, subprocess, urllib.request
import socket, select, http.client, hmac, hashlib, base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", 10000))
TICK = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")
ACTIVE = 10          # keep sampling this many seconds after the last dashboard request
INTERVAL = 2         # sampling period (matches the page's 2s refresh)
DISK_EVERY = 120     # du at most this often, only while watched
DISK_PATHS = [p for p in os.environ.get("DISK_PATHS", "/root,/app,/tmp").split(",") if p]

def envf(name, default):
    try:
        return float((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return float(default)

def rd(p):
    try:
        with open(p) as f:
            return f.read()
    except Exception:
        return None

def redact(c):
    c = re.sub(r"(?i)(authkey|token|password|passwd|secret|key)(=|\s+)\S+", r"\1=***", c)
    return re.sub(r"tskey-\S+", "***", c)

# ---------------- keep-alive self-ping (3s timeout, 5s gap) ----------------
def keepalive():
    url = os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        print("keepalive: no PUBLIC_URL / RENDER_EXTERNAL_URL, self-ping disabled", flush=True)
        return
    url = url.rstrip("/") + "/health"
    while True:
        try:
            urllib.request.urlopen(url, timeout=3).read()
        except Exception:
            pass
        time.sleep(5)

# ---------------- container metrics (cgroup only) ----------------
def cg_mem():
    """(working_set_bytes, limit_bytes_or_None, source) from the cgroup, or (None, None, 'processes')"""
    cur = rd("/sys/fs/cgroup/memory.current")
    if cur is not None:                                   # cgroup v2
        mx, stat, key, src = rd("/sys/fs/cgroup/memory.max"), rd("/sys/fs/cgroup/memory.stat") or "", "inactive_file", "cgroup-v2"
    else:                                                 # cgroup v1
        cur = rd("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        if cur is None:
            return None, None, "processes"
        mx, stat, key, src = (rd("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
                              rd("/sys/fs/cgroup/memory/memory.stat") or "", "total_inactive_file", "cgroup-v1")
    used = int(cur)
    for line in stat.splitlines():
        if line.startswith(key + " "):
            used -= int(line.split()[1])
    try:
        limit = int(mx)
        if limit <= 0 or limit >= 1 << 60:
            limit = None
    except Exception:
        limit = None                                      # "max" = no limit visible
    return max(used, 0), limit, src

def ram_limit(cg_limit):
    env = envf("RAM_LIMIT_MB", 0)
    if env:
        return env * 1048576
    return cg_limit or 512 * 1048576

def cg_cpu_usec():
    for path in ("/sys/fs/cgroup/cpu.stat",):
        for line in (rd(path) or "").splitlines():
            if line.startswith("usage_usec"):
                return int(line.split()[1]) / 1e6
    ns = rd("/sys/fs/cgroup/cpuacct/cpuacct.usage") or rd("/sys/fs/cgroup/cpu,cpuacct/cpuacct.usage")
    return int(ns) / 1e9 if ns else None

def cpu_limit():
    env = envf("CPU_LIMIT", 0)
    if env:
        return env
    m = (rd("/sys/fs/cgroup/cpu.max") or "").split()
    if len(m) == 2 and m[0] != "max":
        try:
            return max(int(m[0]) / int(m[1]), 0.01)
        except Exception:
            pass
    q = rd("/sys/fs/cgroup/cpu/cpu.cfs_quota_us") or rd("/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_quota_us")
    p = rd("/sys/fs/cgroup/cpu/cpu.cfs_period_us") or rd("/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_period_us")
    try:
        if q and int(q) > 0:
            return int(q) / int(p)
    except Exception:
        pass
    return 0.1

def container_uptime():
    """seconds since this container's PID 1 started (/proc/uptime alone is the HOST's)"""
    try:
        host = float((rd("/proc/uptime") or "0").split()[0])
        s = rd("/proc/1/stat")
        start = int(s[s.rindex(")") + 2:].split()[19])
        return max(0, int(host - start / TICK))
    except Exception:
        return 0

def procs():
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        s = rd(f"/proc/{pid}/stat")
        if not s:
            continue
        try:
            name = s[s.index("(") + 1:s.rindex(")")]
            f = s[s.rindex(")") + 2:].split()
            out[int(pid)] = (name, int(f[11]) + int(f[12]), int(f[21]) * PAGE)
        except Exception:
            pass
    return out

def cmdline(pid):
    return redact((rd(f"/proc/{pid}/cmdline") or "").replace("\0", " ").strip())[:90]

def ts_status():
    try:
        r = subprocess.run(["tailscale", "status", "--json", "--peers=false"],
                           capture_output=True, text=True, timeout=3)
        return {"state": json.loads(r.stdout).get("BackendState")}
    except Exception:
        return {"state": "unknown"}

def guard_info():
    alive = False
    try:
        pid = int((rd("/run/oomguard.pid") or "0").strip())
        alive = "oomguard" in (rd(f"/proc/{pid}/cmdline") or "")
    except Exception:
        pass
    try:
        kills = json.loads(rd("/run/oomguard.json") or "[]")[:5]
    except Exception:
        kills = []
    for k in kills:
        k["cmd"] = redact(k.get("cmd", ""))[:90]
    return alive, kills

# ---- disk: du in a separate thread so the sampler never blocks ----
DISK = {"used": 0.0, "at": 0.0, "busy": False}
DISK_LIMIT = envf("DISK_LIMIT_MB", 15360) * 1048576

def own_volume():
    """(used_bytes, total_bytes) of "/" when it is this instance's OWN block device (ext4/xfs on /dev/..) = the real usable disk."""
    try:
        for ln in (rd("/proc/mounts") or "").splitlines():
            f = ln.split()
            if len(f) >= 3 and f[1] == "/" and f[0].startswith("/dev/") and f[2] in ("ext4", "ext3", "xfs", "btrfs"):
                v = os.statvfs("/")
                return (v.f_blocks - v.f_bfree) * v.f_frsize, v.f_blocks * v.f_frsize
    except Exception:
        pass
    return None

def du_scan():
    if not os.environ.get("DISK_LIMIT_MB"):
        vol = own_volume()
        if vol:
            DISK["used"], DISK["total"], DISK["at"], DISK["busy"] = vol[0], vol[1], time.time(), False
            return
    total = 0.0
    cmd0 = ["nice", "-n", "19"] if shutil.which("nice") else []
    for p in DISK_PATHS:
        try:
            out = subprocess.check_output(cmd0 + ["du", "-xsm", p], stderr=subprocess.DEVNULL, timeout=30).decode()
            total += float(out.split()[0]) * 1048576
        except Exception:
            pass
    DISK["used"], DISK["at"], DISK["busy"] = total, time.time(), False


# ---------------- auto port proxy: /<port>/... -> 127.0.0.1:<port> ----------------
# Internal ports that must NEVER be exposed publicly (sshd, tailscale socks, file-manager API, this server itself)
BLOCK = {PORT, 2222, 1055, int(os.environ.get("FS_PORT", 8765) or 8765)}
BLOCK |= {int(x) for x in os.environ.get("PROXY_BLOCK", "").split(",") if x.strip().isdigit()}

# Optional password for ALL /<port>/ routes (HTTP + WebSocket + absolute-path fallback). Unset/empty = open (old behaviour).
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD", "")
AUTH_TOKEN = hmac.new(PROXY_PASSWORD.encode(), b"hermes-proxy-auth", hashlib.sha256).hexdigest() if PROXY_PASSWORD else ""
AUTHCK = re.compile(r"(?:^|;\s*)hp_auth=([0-9a-f]{64})")

def listening_ports():
    """TCP ports with a LISTEN socket right now (auto-detected from /proc/net/tcp{,6}), minus BLOCK."""
    out = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        for ln in (rd(f) or "").splitlines()[1:]:
            p = ln.split()
            if len(p) > 3 and p[3] == "0A":
                try:
                    out.add(int(p[1].rsplit(":", 1)[1], 16))
                except ValueError:
                    pass
    return sorted(x for x in out if x not in BLOCK)

# ---------------- sampler (runs only while the dashboard is being watched) ----------------
S = {"data": None, "last_view": 0.0}

def sampler():
    prev = None
    ts, ts_at = {"state": "?"}, 0
    while True:
        if time.time() - S["last_view"] > ACTIVE:
            prev = None
            time.sleep(1)
            continue
        now = time.time()
        pr = procs()
        cg_s = cg_cpu_usec()
        ticks_total = sum(v[1] for v in pr.values()) / TICK
        cur = {"t": now, "pr": pr, "cg": cg_s, "pt": ticks_total}
        if not DISK["busy"] and now - DISK["at"] > DISK_EVERY:
            DISK["busy"] = True
            threading.Thread(target=du_scan, daemon=True).start()
        if prev is not None:
            dt = max(now - prev["t"], 0.001)
            lim_cpu = cpu_limit()
            used_c, cg_lim, mem_src = cg_mem()
            if used_c is None:
                used_c = sum(v[2] for v in pr.values())
            lim_mem = ram_limit(cg_lim)
            if cg_s is not None and prev["cg"] is not None:
                c_cpu = (cg_s - prev["cg"]) / dt / lim_cpu * 100
            else:
                c_cpu = max(ticks_total - prev["pt"], 0) / dt / lim_cpu * 100
            plist = []
            for pid, (name, ticks, rss) in pr.items():
                old = prev["pr"].get(pid)
                pc = ((ticks - old[1]) / TICK / dt / lim_cpu * 100) if old else 0.0
                plist.append({"pid": pid, "name": name, "cpu": round(min(pc, 999), 1), "rss": rss})
            top = sorted(plist, key=lambda x: (x["cpu"], x["rss"]), reverse=True)[:8]
            top_mem = sorted(plist, key=lambda x: x["rss"], reverse=True)[:8]
            for t in top + top_mem:
                t["cmd"] = cmdline(t["pid"])
            if now - ts_at > 10:
                ts, ts_at = ts_status(), now
            names = {p["name"] for p in plist}
            g_alive, kills = guard_info()
            alerts = []
            if c_cpu > 85: alerts.append(f"CPU high: {c_cpu:.0f}% of {lim_cpu:g} vCPU")
            if used_c / lim_mem > 0.85: alerts.append(f"RAM high: {used_c / lim_mem * 100:.0f}%")
            if DISK["used"] / DISK.get("total", DISK_LIMIT) > 0.90: alerts.append("Disk almost full")
            if ts.get("state") != "Running": alerts.append(f"Tailscale state: {ts.get('state')}")
            if "sshd" not in names: alerts.append("sshd is NOT running")
            if not g_alive: alerts.append("OOM guard is NOT running")
            if kills:
                try:
                    age = now - time.mktime(time.strptime(kills[0]["t"], "%Y-%m-%d %H:%M:%S"))
                    if age < 600: alerts.append(f"OOM guard killed {kills[0]['name']} (PID {kills[0]['pid']}) at {kills[0]['t']}")
                except Exception: pass
            S["data"] = {
                "time": time.strftime("%H:%M:%S"), "uptime": container_uptime(),
                "cpus": round(lim_cpu, 3), "c_cpu": round(min(c_cpu, 100), 1),
                "c_mem": used_c, "c_mem_limit": lim_mem,
                "disk": DISK["used"], "disk_total": DISK.get("total", DISK_LIMIT), "disk_at": DISK["at"],
                "src": mem_src, "ts": ts, "nproc": len(pr), "guard": g_alive, "kills": kills,
                "top_cpu": top, "top_mem": top_mem, "alerts": alerts, "ports": listening_ports(), "backup": json.loads(rd("/run/hermes-backup.json") or "null"),
            }
        prev = cur
        time.sleep(INTERVAL)

DASH_HTML = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>hermes</title>
<style>
body{margin:0;font:14px ui-monospace,Menlo,monospace;background:#0e1116;color:#d6dde6;padding:14px}
h1{font-size:16px;margin:0 0 10px}.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
.c{background:#161b22;border:1px solid #2a313c;border-radius:8px;padding:10px}.c b{display:block;margin-bottom:6px;color:#8b98a9;font-weight:600}
.bar{height:8px;background:#2a313c;border-radius:4px;overflow:hidden;margin:4px 0}.bar i{display:block;height:100%;background:#3fb950;width:0}
.w i{background:#d29922}.d i{background:#f85149}
table{width:100%;border-collapse:collapse;margin-top:6px}td,th{padding:3px 6px;text-align:left;border-bottom:1px solid #222a35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:260px}
a{color:#58a6ff}th{color:#8b98a9}.al{background:#3b1418;border:1px solid #f85149;border-radius:8px;padding:8px 10px;margin-bottom:10px}.ok{color:#3fb950}
@media(max-width:600px){td:nth-child(4){display:none}th:nth-child(4){display:none}}
</style>
<h1>hermes-cloud <span id=t></span></h1><div id=al></div>
<div class=g id=cards></div>
<div class=c style="margin-top:10px"><b>Top by CPU (% of vCPU limit)</b><table id=tc></table></div>
<div class=c style="margin-top:10px"><b>Top by RAM</b><table id=tm></table></div>
<div class=c style="margin-top:10px"><b id=gh>OOM guard</b><table id=tk></table></div>
<script>
const $=id=>document.getElementById(id);
const mb=b=>b>=1073741824?(b/1073741824).toFixed(2)+' GB':(b/1048576).toFixed(0)+' MB';
const cls=p=>p>85?'d':p>65?'w':'';
function card(t,p,txt){return `<div class="c ${cls(p)}"><b>${t}</b>${txt}<div class=bar><i style="width:${Math.min(p,100)}%"></i></div></div>`}
function rows(el,l){el.innerHTML='<tr><th>PID<th>Name<th>CPU%<th>Command<th>RAM</tr>';
 l.forEach(p=>{const r=el.insertRow();[p.pid,p.name,p.cpu,p.cmd,mb(p.rss)].forEach(v=>{r.insertCell().textContent=v})})}
function up(s){const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return (d?d+'d ':'')+h+'h '+m+'m'}
async function tick(){
 if(document.hidden){setTimeout(tick,2000);return}
 try{const d=await (await fetch('/api',{cache:'no-store'})).json();
  if(d.warming){setTimeout(tick,1000);return}
  $('t').textContent=d.time+' · up '+up(d.uptime);
  $('al').innerHTML=d.alerts.length?'<div class=al>⚠ '+d.alerts.join('<br>⚠ ')+'</div>':'';
  document.title=(d.alerts.length?'⚠ ':'')+'hermes';
  const cp=d.c_mem/d.c_mem_limit*100,dp=d.disk_total?d.disk/d.disk_total*100:0;
  $('cards').innerHTML=
   card('CPU ('+d.cpus+' vCPU)',d.c_cpu,d.c_cpu+'%')+
   card('RAM',cp,mb(d.c_mem)+' / '+mb(d.c_mem_limit))+
   card('Disk',dp,d.disk_at?mb(d.disk)+' / '+mb(d.disk_total):'measuring…')+
   `<div class=c><b>Tailscale</b><span class="${d.ts.state=='Running'?'ok':''}">${d.ts.state}</span><br>${d.nproc} processes</div>`;
  $('cards').innerHTML+='<div class=c><b>Open ports (public URL)</b>'+(d.ports.length?d.ports.map(p=>'<a href="/'+p+'/" target=_blank>/'+p+'/</a>').join(' &nbsp; '):'none yet')+'</div>';
  if(d.backup&&d.backup.state!='disabled'){const b=d.backup;$('cards').innerHTML+='<div class=c><b>Backup (rclone)</b>'+b.state+(b.last_ok?' · last ok '+b.last_ok:'')+'<br>'+b.units+' units, '+b.uploaded+' uploaded last cycle'+(b.pending&&b.pending.length?'<br>restore pending: '+b.pending.length:'')+(b.last_error?'<br>'+b.last_error:'')+'</div>'}
  rows($('tc'),d.top_cpu);rows($('tm'),d.top_mem);
  $('gh').textContent='OOM guard: '+(d.guard?'active':'NOT RUNNING')+(d.kills.length?' · recent kills':' · no kills yet');
  const k=$('tk');k.innerHTML='';d.kills.forEach(x=>{const r=k.insertRow();[x.t,x.pid,x.name,mb(x.rss),x.free_pct+'% free',x.cmd].forEach(v=>{r.insertCell().textContent=v})});
 }catch(e){$('al').innerHTML='<div class=al>⚠ connection lost</div>'}
 setTimeout(tick,2000)}
tick()
</script>"""

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "proxy-connection"}
PFX = re.compile(r"^/(\d{1,5})(?=/|\?|$)")                       # /8097/...  (public path prefix)
REF = re.compile(r"^https?://[^/]+/(\d{1,5})(?=/|\?|$)")         # Referer: https://host/8097/...
CKP = re.compile(r"(?:^|;\s*)hp_port=(\d{1,5})")                 # remembered port (for apps using absolute /paths)
RESERVED = ("/", "/api", "/health")

def is_open(port):
    if not (0 < port < 65536) or port in BLOCK:
        return False
    try:
        socket.create_connection(("localhost", port), timeout=0.4).close()
        return True
    except OSError:
        return False

class Lim:
    """file-like that yields exactly n bytes of the request body (streamed, not buffered)"""
    def __init__(self, f, n): self.f, self.n = f, n
    def read(self, amt=-1):
        if self.n <= 0: return b""
        data = self.f.read(self.n if amt is None or amt < 0 else min(amt, self.n))
        self.n -= len(data)
        return data

def read_chunked(f):
    out = b""
    while True:
        size = int((f.readline() or b"0").split(b";")[0].strip() or b"0", 16)
        if size == 0:
            while f.readline().strip(): pass
            return out
        out += f.read(size); f.readline()

class H(BaseHTTPRequestHandler):
    # ---- routing: which internal port (if any) does this request belong to? ----
    def route(self):
        """-> ("proxy", port, upstream_path, prefixed) | ("redirect", url) | ("local",) | ("down", port)"""
        raw = self.path
        path = raw.split("?")[0]
        m = PFX.match(raw)
        down = None
        if m:
            port = int(m.group(1))
            if port not in BLOCK and 0 < port < 65536:
                if is_open(port):
                    rest = raw[m.end():]
                    if rest == "":
                        return ("redirect", raw + "/")
                    if rest.startswith("?"):
                        rest = "/" + rest
                    return ("proxy", port, rest, True)
                down = port
        # fallback for apps that use absolute paths (/static/x.js, /socket.io/): Referer, then cookie
        r = REF.match(self.headers.get("Referer", ""))
        if r and is_open(int(r.group(1))):
            return ("proxy", int(r.group(1)), raw, False)
        if path not in RESERVED:
            c = CKP.search(self.headers.get("Cookie", ""))
            if c and is_open(int(c.group(1))):
                return ("proxy", int(c.group(1)), raw, False)
        if down:
            return ("down", down)
        return ("local",)

    def authed(self):
        """True if no password is configured, or the request carries it as: hp_auth cookie, Basic auth (any user name),
        or X-Proxy-Password header. A fresh header/Basic login sets self.new_auth so a cookie is issued (and Authorization is not forwarded)."""
        self.new_auth = False
        if not PROXY_PASSWORD:
            return True
        c = AUTHCK.search(self.headers.get("Cookie", ""))
        if c and hmac.compare_digest(c.group(1), AUTH_TOKEN):
            return True
        cand = [self.headers.get("X-Proxy-Password", "")]
        a = self.headers.get("Authorization", "")
        if a.lower().startswith("basic "):
            try:
                cand.append(base64.b64decode(a[6:]).decode("utf-8", "replace").split(":", 1)[-1])
            except Exception:
                pass
        for p in cand:
            if p and hmac.compare_digest(p.encode(), PROXY_PASSWORD.encode()):
                self.new_auth = True
                return True
        return False

    def auth_cookie(self):
        sec = "; Secure" if self.headers.get("X-Forwarded-Proto", "") == "https" else ""
        return f"hp_auth={AUTH_TOKEN}; Path=/; HttpOnly; SameSite=Lax{sec}"

    def reply(self, code, body, ctype="text/plain; charset=utf-8", extra=()):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store")
        for k, v in extra: self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(b)

    def fwd_headers(self, port, prefixed):
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
        h = []
        for k, v in self.headers.items():
            kl = k.lower()
            if kl in HOP and kl != "upgrade" or kl in ("host", "content-length", "x-forwarded-host"):
                continue
            if PROXY_PASSWORD:                           # never leak our credentials to the apps
                if kl == "x-proxy-password" or (kl == "authorization" and getattr(self, "new_auth", False)):
                    continue
                if kl == "cookie":
                    v = re.sub(r"(?:^|;\s*)hp_auth=[^;]*", "", v).strip("; ")
                    if not v:
                        continue
            if kl == "origin":
                v = f"http://localhost:{port}"          # makes CSRF/origin checks of dev servers pass
            elif kl == "referer":
                v = REF.sub(f"http://localhost:{port}", v, 1) if REF.match(v) else v
            h.append((k, v))
        ip = self.client_address[0]
        xff = self.headers.get("X-Forwarded-For")
        h += [("Host", f"localhost:{port}"), ("X-Forwarded-Host", host),
              ("X-Forwarded-Proto", self.headers.get("X-Forwarded-Proto", "https")),
              ("X-Forwarded-For", f"{xff}, {ip}" if xff else ip)]
        if prefixed:
            h.append(("X-Forwarded-Prefix", f"/{port}"))
        return h

    # ---- plain HTTP proxy (streams the response: SSE, downloads, long polling all work) ----
    def proxy_http(self, port, upath, prefixed):
        hdrs = dict(self.fwd_headers(port, prefixed))
        body = None
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            body = read_chunked(self.rfile); hdrs["Content-Length"] = str(len(body))
        else:
            n = int(self.headers.get("Content-Length") or 0)
            if n: body = Lim(self.rfile, n); hdrs["Content-Length"] = str(n)
        hdrs["Connection"] = "close"
        try:
            up = http.client.HTTPConnection("localhost", port, timeout=30)
            up.request(self.command, upath, body=body, headers=hdrs)
            r = up.getresponse()
            sk = up.sock or getattr(getattr(r.fp, "raw", None), "_sock", None)   # sock is None when upstream sends Connection: close
            if sk: sk.settimeout(3600)                                            # allow long-lived streams (SSE, downloads)
        except Exception as e:
            return self.reply(502, f"Bad gateway: port {port} did not answer ({e})")
        self.close_connection = True
        self.send_response(r.status, r.reason)
        for k, v in r.getheaders():
            kl = k.lower()
            if kl in HOP or kl == "server" or kl == "date":
                continue
            if kl == "location":
                if v.startswith("/") and not v.startswith("//"):
                    v = f"/{port}{v}" if prefixed else v
                else:
                    v = re.sub(r"^https?://(?:localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0):%d(?=/|$)" % port,
                               f"/{port}" if prefixed else "", v) or "/"
            self.send_header(k, v)
        if prefixed:
            self.send_header("Set-Cookie", f"hp_port={port}; Path=/; SameSite=Lax")
        if PROXY_PASSWORD and self.new_auth:
            self.send_header("Set-Cookie", self.auth_cookie())
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            if self.command != "HEAD":
                while True:
                    chunk = r.read1(65536)
                    if not chunk: break
                    self.wfile.write(chunk); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            up.close()

    # ---- WebSocket / any Upgrade: raw bidirectional pipe ----
    def proxy_tunnel(self, port, upath, prefixed):
        try:
            up = socket.create_connection(("localhost", port), timeout=10)
        except OSError as e:
            return self.reply(502, f"Bad gateway: port {port} did not answer ({e})")
        up.settimeout(None)
        lines = [f"{self.command} {upath} HTTP/1.1", "Connection: Upgrade"]
        lines += [f"{k}: {v}" for k, v in self.fwd_headers(port, prefixed)]
        up.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        self.close_connection = True
        cl = self.connection
        try:
            while True:
                rs, _, _ = select.select([cl, up], [], [], 3600)
                if not rs: break
                for s in rs:
                    data = s.recv(65536)
                    if not data: return
                    (up if s is cl else cl).sendall(data)
        except OSError:
            pass
        finally:
            up.close()

    def handle_any(self):
        rt = self.route()
        if rt[0] in ("redirect", "proxy", "down") and not self.authed():
            return self.reply(401, "Password required (username: anything, password: PROXY_PASSWORD)",
                              extra=[("WWW-Authenticate", 'Basic realm="hermes ports", charset="UTF-8"')])
        if rt[0] == "redirect":
            return self.reply(308, "", extra=[("Location", rt[1])])
        if rt[0] == "proxy":
            _, port, upath, prefixed = rt
            if self.headers.get("Upgrade") and "upgrade" in self.headers.get("Connection", "").lower():
                return self.proxy_tunnel(port, upath, prefixed)
            return self.proxy_http(port, upath, prefixed)
        if rt[0] == "down":
            return self.reply(502, f"Nothing is listening on port {rt[1]} yet. Start your app on that port "
                                   f"(bind 0.0.0.0 or 127.0.0.1) and refresh.")
        if self.command not in ("GET", "HEAD"):
            return self.reply(404, "not found")
        p = self.path.split("?")[0]
        if p == "/api":
            S["last_view"] = time.time()
            body, ctype = json.dumps(S["data"] or {"warming": True}).encode(), "application/json"
        elif p == "/":
            S["last_view"] = time.time()
            body, ctype = DASH_HTML.encode(), "text/html; charset=utf-8"
        else:  # /health and anything else: plain ok, does NOT wake the sampler
            body, ctype = b"ok", "text/plain"
        self.reply(200, body, ctype)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = handle_any
    def log_message(self, *a): pass

def serve(port, host, h):
    s = ThreadingHTTPServer((host, port), h); s.daemon_threads = True; s.serve_forever()

if __name__ == "__main__":
    try:                                   # file-manager API: 127.0.0.1 only, published to the tailnet by start.sh
        import fsd; fsd.start()
    except Exception as e:
        print(f"fsd: not started: {e}", flush=True)
    threading.Thread(target=keepalive, daemon=True).start()
    threading.Thread(target=sampler, daemon=True).start()
    serve(PORT, "0.0.0.0", H)
