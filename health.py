"""Single public server: /health -> 'ok' (used by self-ping), / -> read-only dashboard, /api -> JSON.
No shell, no write endpoints. Dashboard samples /proc only while someone is viewing it, so idle cost is ~0 and
extra viewers / the 5s self-ping never add load (everyone reads one cached sample)."""
import os, re, json, time, threading, subprocess, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", 10000))
NCPU = os.cpu_count() or 1
TICK = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")
ACTIVE = 10          # seconds after last dashboard request during which we keep sampling
INTERVAL = 2         # sampling period (matches the page's 2s refresh)

def rd(p):
    try:
        with open(p) as f:
            return f.read()
    except Exception:
        return None

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

# ---------------- metrics ----------------
def meminfo():
    d = {}
    for line in (rd("/proc/meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        try:
            d[k] = int(v.split()[0]) * 1024
        except Exception:
            pass
    return d

def cg_mem(host_total):
    cur = rd("/sys/fs/cgroup/memory.current")
    mx = rd("/sys/fs/cgroup/memory.max")
    stat = rd("/sys/fs/cgroup/memory.stat") or ""
    key = "inactive_file"
    if cur is None:  # cgroup v1
        cur = rd("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        mx = rd("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        stat = rd("/sys/fs/cgroup/memory/memory.stat") or ""
        key = "total_inactive_file"
    if cur is None:
        return None, host_total
    used = int(cur)
    for line in stat.splitlines():
        if line.startswith(key + " "):
            used -= int(line.split()[1])  # working set, like docker stats
    try:
        limit = int(mx)
        if limit <= 0 or limit > host_total:
            limit = host_total
    except Exception:
        limit = host_total
    return max(used, 0), limit

def cg_cpu():
    """returns (usage_usec, cpus_limit) or None"""
    s = rd("/sys/fs/cgroup/cpu.stat")
    if not s:
        return None
    usage = None
    for line in s.splitlines():
        if line.startswith("usage_usec"):
            usage = int(line.split()[1])
    cpus = NCPU
    m = (rd("/sys/fs/cgroup/cpu.max") or "").split()
    if len(m) == 2 and m[0] != "max":
        try:
            cpus = max(int(m[0]) / int(m[1]), 0.01)
        except Exception:
            pass
    return (usage, cpus) if usage is not None else None

def host_cpu_ticks():
    p = (rd("/proc/stat") or "").split("\n", 1)[0].split()[1:]
    v = [int(x) for x in p]
    idle = v[3] + (v[4] if len(v) > 4 else 0)
    return sum(v) - idle, sum(v)

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

def redact(c):
    c = re.sub(r"(?i)(authkey|token|password|passwd|secret|key)(=|\s+)\S+", r"\1=***", c)
    return re.sub(r"tskey-\S+", "***", c)

def cmdline(pid):
    return redact((rd(f"/proc/{pid}/cmdline") or "").replace("\0", " ").strip())[:90]

def guard_info():
    """OOM guard status + recent kills (written by oomguard.py)"""
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

def ts_status():
    try:
        r = subprocess.run(["tailscale", "status", "--json", "--peers=false"],
                           capture_output=True, text=True, timeout=3)
        j = json.loads(r.stdout)
        return {"state": j.get("BackendState"), "ip": ""}
    except Exception:
        return {"state": "unknown", "ip": ""}

# ---------------- sampler (runs only while the dashboard is being watched) ----------------
S = {"data": None, "last_view": 0.0}

def sampler():
    prev = None
    ts = {"state": "?", "ip": ""}
    ts_at = 0
    while True:
        if time.time() - S["last_view"] > ACTIVE:
            prev = None
            time.sleep(1)
            continue
        now = time.time()
        pr = procs()
        cg = cg_cpu()
        hb, ht = host_cpu_ticks()
        cur = {"t": now, "pr": pr, "cg": cg[0] if cg else None, "hb": hb, "ht": ht}
        if prev is not None:
            dt = max(now - prev["t"], 0.001)
            mi = meminfo()
            host_total = mi.get("MemTotal", 1)
            host_used = host_total - mi.get("MemAvailable", 0)
            c_used, c_limit = cg_mem(host_total)
            if c_used is None:
                c_used, c_limit = host_used, host_total
            cpus = cg[1] if cg else NCPU
            if cg and prev["cg"] is not None:
                c_cpu = (cg[0] - prev["cg"]) / 1e6 / dt / cpus * 100
            else:
                c_cpu = (hb - prev["hb"]) / max(ht - prev["ht"], 1) * 100
            h_cpu = (hb - prev["hb"]) / max(ht - prev["ht"], 1) * 100
            plist = []
            for pid, (name, ticks, rss) in pr.items():
                old = prev["pr"].get(pid)
                pc = ((ticks - old[1]) / TICK / dt * 100) if old else 0.0
                plist.append({"pid": pid, "name": name, "cpu": round(pc, 1), "rss": rss})
            top = sorted(plist, key=lambda x: (x["cpu"], x["rss"]), reverse=True)[:8]
            for t in top:
                t["cmd"] = cmdline(t["pid"])
            top_mem = sorted(plist, key=lambda x: x["rss"], reverse=True)[:8]
            for t in top_mem:
                t["cmd"] = cmdline(t["pid"])
            if now - ts_at > 10:
                ts, ts_at = ts_status(), now
            try:
                st = os.statvfs("/")
                disk_total, disk_used = st.f_blocks * st.f_frsize, (st.f_blocks - st.f_bavail) * st.f_frsize
            except Exception:
                disk_total = disk_used = 0
            la = (rd("/proc/loadavg") or "0 0 0").split()[:3]
            names = {p["name"] for p in plist}
            alerts = []
            if c_cpu > 85: alerts.append(f"Container CPU high: {c_cpu:.0f}%")
            if c_limit and c_used / c_limit > 0.85: alerts.append(f"Container RAM high: {c_used / c_limit * 100:.0f}%")
            if host_total and host_used / host_total > 0.90: alerts.append("HOST RAM high (shared server under pressure)")
            if h_cpu > 90: alerts.append(f"HOST CPU high: {h_cpu:.0f}% (shared server busy)")
            if disk_total and disk_used / disk_total > 0.90: alerts.append("Disk almost full")
            if ts.get("state") != "Running": alerts.append(f"Tailscale state: {ts.get('state')}")
            if "sshd" not in names: alerts.append("sshd is NOT running")
            g_alive, kills = guard_info()
            if not g_alive: alerts.append("OOM guard is NOT running")
            if kills:
                try:
                    age = time.time() - time.mktime(time.strptime(kills[0]["t"], "%Y-%m-%d %H:%M:%S"))
                    if age < 600: alerts.append(f"OOM guard killed {kills[0]['name']} (PID {kills[0]['pid']}) at {kills[0]['t']}")
                except Exception: pass
            S["data"] = {
                "time": time.strftime("%H:%M:%S"), "uptime": int(float((rd("/proc/uptime") or "0").split()[0])),
                "cpus": round(cpus, 2), "c_cpu": round(c_cpu, 1), "h_cpu": round(h_cpu, 1),
                "c_mem": c_used, "c_mem_limit": c_limit, "h_mem": host_used, "h_mem_total": host_total,
                "disk": disk_used, "disk_total": disk_total, "load": la, "ts": ts,
                "nproc": len(pr), "guard": g_alive, "kills": kills, "top_cpu": top, "top_mem": top_mem, "alerts": alerts,
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
<div class=c style="margin-top:10px"><b>Top by CPU</b><table id=tc></table></div>
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
  const cp=d.c_mem/d.c_mem_limit*100,hp=d.h_mem/d.h_mem_total*100,dp=d.disk_total?d.disk/d.disk_total*100:0;
  $('cards').innerHTML=
   card('Container CPU ('+d.cpus+' vCPU)',d.c_cpu,d.c_cpu+'%')+
   card('Container RAM',cp,mb(d.c_mem)+' / '+mb(d.c_mem_limit))+
   card('Host CPU (shared)',d.h_cpu,d.h_cpu+'%  · load '+d.load.join(' '))+
   card('Host RAM (shared)',hp,mb(d.h_mem)+' / '+mb(d.h_mem_total))+
   card('Disk /',dp,mb(d.disk)+' / '+mb(d.disk_total))+
   `<div class=c><b>Tailscale</b><span class="${d.ts.state=='Running'?'ok':''}">${d.ts.state}</span> ${d.ts.ip}<br>${d.nproc} processes</div>`;
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
