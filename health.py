"""Single public server: /health -> 'ok' (self-ping), / -> read-only dashboard, /api -> JSON. No shell, no write endpoints.

Shows ONLY this container's plan allotment (cgroup) - never the host machine:
  CPU  = cgroup cpu.stat vs the vCPU quota        RAM = cgroup working set (page cache excluded) vs the cgroup limit
  Disk = `du` of /root,/app,/tmp vs DISK_LIMIT_MB  Uptime = since the container's PID 1 started
Limits that cannot be read from inside can be set: CPU_LIMIT (vCPU), RAM_LIMIT_MB, DISK_LIMIT_MB
(defaults when nothing is visible: 0.1 vCPU / 512 MB / 15 GB, same as the hermes repo).
Sampling runs only while somebody views "/" or "/api" (idle cost ~0); /health never wakes it."""
import os, re, json, time, shutil, threading, subprocess, urllib.request
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

def du_scan():
    total = 0.0
    cmd0 = ["nice", "-n", "19"] if shutil.which("nice") else []
    for p in DISK_PATHS:
        try:
            out = subprocess.check_output(cmd0 + ["du", "-xsm", p], stderr=subprocess.DEVNULL, timeout=30).decode()
            total += float(out.split()[0]) * 1048576
        except Exception:
            pass
    DISK["used"], DISK["at"], DISK["busy"] = total, time.time(), False

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
            if DISK["used"] / DISK_LIMIT > 0.90: alerts.append("Disk almost full")
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
                "disk": DISK["used"], "disk_total": DISK_LIMIT, "disk_at": DISK["at"],
                "src": mem_src, "ts": ts, "nproc": len(pr), "guard": g_alive, "kills": kills,
                "top_cpu": top, "top_mem": top_mem, "alerts": alerts,
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
th{color:#8b98a9}.al{background:#3b1418;border:1px solid #f85149;border-radius:8px;padding:8px 10px;margin-bottom:10px}.ok{color:#3fb950}
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
  rows($('tc'),d.top_cpu);rows($('tm'),d.top_mem);
  $('gh').textContent='OOM guard: '+(d.guard?'active':'NOT RUNNING')+(d.kills.length?' · recent kills':' · no kills yet');
  const k=$('tk');k.innerHTML='';d.kills.forEach(x=>{const r=k.insertRow();[x.t,x.pid,x.name,mb(x.rss),x.free_pct+'% free',x.cmd].forEach(v=>{r.insertCell().textContent=v})});
 }catch(e){$('al').innerHTML='<div class=al>⚠ connection lost</div>'}
 setTimeout(tick,2000)}
tick()
</script>"""

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api":
            S["last_view"] = time.time()
            body, ctype = json.dumps(S["data"] or {"warming": True}).encode(), "application/json"
        elif p == "/":
            S["last_view"] = time.time()
            body, ctype = DASH_HTML.encode(), "text/html; charset=utf-8"
        else:  # /health and anything else: plain ok, does NOT wake the sampler
            body, ctype = b"ok", "text/plain"
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store"); self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
    do_HEAD = do_GET
    def log_message(self, *a): pass

def serve(port, host, h):
    s = ThreadingHTTPServer((host, port), h); s.daemon_threads = True; s.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=keepalive, daemon=True).start()
    threading.Thread(target=sampler, daemon=True).start()
    serve(PORT, "0.0.0.0", H)
