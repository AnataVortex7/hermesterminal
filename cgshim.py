#!/usr/bin/env python3
"""cgshim: container-aware replacements for free / uptime / nproc / top / df / lscpu.

Installed as symlinks in /usr/local/bin (which comes BEFORE /usr/bin in PATH), so when anyone
(or any agent) runs `free -m`, `nproc`, `top -bn1`, `df -h`, `uptime`, `lscpu` over SSH they see
THIS container's plan (cgroup limits / RAM_LIMIT_MB / CPU_LIMIT / DISK_LIMIT_MB), never the host.
Anything unsupported (other flags/paths) falls through to the real binary.
"""
import os, sys, time, math, subprocess, shutil

def rd(p):
    try:
        with open(p) as f: return f.read()
    except Exception: return None

def env(name, default=0.0):
    v = os.environ.get(name)
    if v is None:                                   # ssh sessions don't inherit the container env -> read PID 1's
        raw = rd("/proc/1/environ") or ""
        for kv in raw.split("\0"):
            if kv.startswith(name + "="): v = kv.split("=", 1)[1]
    try: return float(v) if v else default
    except ValueError: return default

# ---------- limits ----------
def mem():
    """(used, limit, cache, shared, cur) in bytes; used excludes page cache (= what really counts)"""
    cur = rd("/sys/fs/cgroup/memory.current")
    if cur is not None:
        mx, stat, kf, ks = rd("/sys/fs/cgroup/memory.max"), rd("/sys/fs/cgroup/memory.stat") or "", ("inactive_file", "active_file"), "shmem"
    else:
        cur = rd("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        mx, stat, kf, ks = rd("/sys/fs/cgroup/memory/memory.limit_in_bytes"), rd("/sys/fs/cgroup/memory/memory.stat") or "", ("total_inactive_file", "total_active_file"), "total_shmem"
    cur = int(cur) if cur else 0
    cache = shared = 0
    for line in stat.splitlines():
        k, _, v = line.partition(" ")
        if k in kf: cache += int(v)
        elif k == ks: shared = int(v)
    lim = env("RAM_LIMIT_MB") * 1048576
    if not lim:
        try:
            v = int(mx)
            if 0 < v < 1 << 60: lim = v
        except Exception: pass
    lim = lim or 512 * 1048576
    return max(cur - cache, 0), int(lim), cache, shared, cur

def cpu_limit():
    e = env("CPU_LIMIT")
    if e: return e
    m = (rd("/sys/fs/cgroup/cpu.max") or "").split()
    if len(m) == 2 and m[0] != "max":
        try: return max(int(m[0]) / int(m[1]), 0.01)
        except Exception: pass
    q = rd("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"); p = rd("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    try:
        if q and int(q) > 0: return int(q) / int(p)
    except Exception: pass
    return 0.1

def cpu_sec():
    for line in (rd("/sys/fs/cgroup/cpu.stat") or "").splitlines():
        if line.startswith("usage_usec"): return int(line.split()[1]) / 1e6
    ns = rd("/sys/fs/cgroup/cpuacct/cpuacct.usage") or rd("/sys/fs/cgroup/cpu,cpuacct/cpuacct.usage")
    return int(ns) / 1e9 if ns else 0.0

def cpu_pct(interval=0.4):
    """container CPU as % of its own quota (100% = quota fully used)"""
    a, t = cpu_sec(), time.time(); time.sleep(interval)
    b, u = cpu_sec(), time.time()
    return max(0.0, (b - a) / (u - t) / cpu_limit() * 100)

def uptime_s():
    try:
        host = float(rd("/proc/uptime").split()[0]); s = rd("/proc/1/stat")
        return max(0, int(host - int(s[s.rindex(")") + 2:].split()[19]) / os.sysconf("SC_CLK_TCK")))
    except Exception: return 0

def disk():
    lim = env("DISK_LIMIT_MB", 15360) * 1048576
    tot = 0
    for p in ("/root", "/app", "/tmp"):
        try: tot += float(subprocess.check_output(["du", "-xsm", p], stderr=subprocess.DEVNULL, timeout=30).split()[0]) * 1048576
        except Exception: pass
    return int(tot), int(lim)

def real(name):
    """exec the real binary (skipping /usr/local/bin where this shim lives)"""
    for d in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        p = os.path.join(d, name)
        if os.path.exists(p): os.execv(p, [name] + sys.argv[1:])
    sys.exit(f"{name}: not found")

# ---------- commands ----------
def cmd_free(a):
    div, human = 1024, False                        # default KiB like real free
    for x in a:
        if x == "-b": div = 1
        elif x == "-k": div = 1024
        elif x in ("-m", "--mebi"): div = 1024 ** 2
        elif x in ("-g", "--gibi"): div = 1024 ** 3
        elif x in ("-h", "--human"): human = True
    used, lim, cache, shared, cur = mem()
    free_ = max(lim - cur, 0); avail = max(lim - used, 0)
    def f(n):
        if not human: return str(int(n // div))
        n = float(n)
        for u in ("B", "Ki", "Mi", "Gi"):
            if n < 1024 or u == "Gi": return f"{n:.0f}{u}" if u == "B" else f"{n:.1f}{u}"
            n /= 1024
    rows = [("", "total", "used", "free", "shared", "buff/cache", "available"),
            ("Mem:", f(lim), f(used), f(free_), f(shared), f(cache), f(avail)),
            ("Swap:", f(0), f(0), f(0))]
    for r in rows: print(f"{r[0]:<8}" + "".join(f"{c:>12}" for c in r[1:]))

def cmd_nproc(a):
    print(max(1, math.ceil(cpu_limit())))

def cmd_uptime(a):
    s = uptime_s(); d, r = divmod(s, 86400); h, r = divmod(r, 3600); m = r // 60
    up = (f"{d} days, " if d else "") + f"{h}:{m:02d}"
    pct = cpu_pct(0.3)
    print(f" {time.strftime('%H:%M:%S')} up {up},  cpu {pct:.0f}% of {cpu_limit():g} vCPU quota")

def cmd_top(a):
    used, lim, cache, shared, cur = mem(); pct = cpu_pct(); dk, dl = disk()
    mb = lambda b: f"{b / 1048576:.1f}"
    print(f"top - {time.strftime('%H:%M:%S')} up {uptime_s() // 3600}h  [container plan: {cpu_limit():g} vCPU / {lim // 1048576} MB RAM / {dl // 1073741824} GB disk]")
    print(f"CPU : {pct:.1f}% of {cpu_limit():g} vCPU quota")
    print(f"MiB Mem : {mb(lim):>9} total, {mb(max(lim - used, 0)):>9} free, {mb(used):>9} used, {mb(cache):>9} buff/cache")
    print(f"Disk: {dk / 1048576:.0f} MB used of {dl // 1048576} MB\n")
    sys.stdout.flush()
    out = subprocess.run(["/usr/bin/ps", "-eo", "pid,user,rss,etime,args", "--sort=-rss"], capture_output=True, text=True).stdout.splitlines()
    for l in out[:16]: print(l[:160])

def cmd_df(a):
    # only take over the plain `df`, `df -h`, `df -m`, `df -k`, `df -H`, `df -T` forms; real paths go to the real df
    flags = [x for x in a if x.startswith("-")]
    if len(flags) != len(a) or any(x not in ("-h", "-H", "-k", "-m", "-T", "--human-readable") for x in a): real("df")
    u, lim = disk(); av = max(lim - u, 0)
    human = "-h" in a or "-H" in a or "--human-readable" in a
    if human:
        def f(n):
            n = float(n)
            for s in ("B", "K", "M", "G", "T"):
                if n < 1024: return f"{n:.0f}{s}" if n >= 10 or s == "B" else f"{n:.1f}{s}"
                n /= 1024
    else:
        d = 1048576 if "-m" in a else 1024
        f = lambda n: str(int(n // d))
    print(f"{'Filesystem':<14}{'Size':>8}{'Used':>8}{'Avail':>8}{'Use%':>6} Mounted on")
    print(f"{'container':<14}{f(lim):>8}{f(u):>8}{f(av):>8}{int(u / lim * 100):>5}% /")

def cmd_lscpu(a):
    model = next((l.split(":", 1)[1].strip() for l in (rd("/proc/cpuinfo") or "").splitlines() if l.startswith("model name")), "unknown")
    n = max(1, math.ceil(cpu_limit()))
    print(f"{'CPU(s):':<22}{n}\n{'Model name:':<22}{model}\n{'CPU quota (vCPU):':<22}{cpu_limit():g}   <- this container's plan; host CPUs are not available to you")

CMDS = {"free": cmd_free, "nproc": cmd_nproc, "uptime": cmd_uptime, "top": cmd_top, "df": cmd_df, "lscpu": cmd_lscpu}

if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)      # `top | head` must not print a traceback
    name = os.path.basename(sys.argv[0])
    if name not in CMDS: sys.exit("cgshim: run via symlink: " + ", ".join(CMDS))
    CMDS[name](sys.argv[1:])
