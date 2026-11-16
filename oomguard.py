#!/usr/bin/env python3
"""OOM guard: when free RAM (of the cgroup limit) drops below OOM_FREE_PCT (default 8%),
instantly SIGKILL the biggest non-protected process and tell whoever ran it
(message is written to the victim's stderr/stdout, i.e. back down the SSH session)."""
import os, sys, time, json, signal

FREE_PCT = float(os.environ.get("OOM_FREE_PCT", 10))
POLL = float(os.environ.get("OOM_POLL_MS", 25)) / 1000
STATE = os.environ.get("OOM_STATE", "/run/oomguard.json")
PIDFILE = os.environ.get("OOM_PIDFILE", "/run/oomguard.pid")
LOG = os.environ.get("OOM_LOG", "/var/log/oomguard.log")
FORCE_LIMIT = int(os.environ.get("OOM_LIMIT_BYTES", 0))
PAGE = os.sysconf("SC_PAGE_SIZE")
SELF = os.getpid()
PROTECT_NAMES = {"tailscaled", "tailscale", "sshd"}
PROTECT_CMD = ("start.sh", "health.py", "oomguard.py")

def rd(p):
    try:
        with open(p) as f: return f.read()
    except Exception: return None

V2 = os.path.exists("/sys/fs/cgroup/memory.current")
if V2:
    CUR, STAT, MAX = "/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.stat", "/sys/fs/cgroup/memory.max"
    KEYS = (b"inactive_file ", b"active_file ")
elif os.path.exists("/sys/fs/cgroup/memory/memory.usage_in_bytes"):
    CUR, STAT, MAX = ("/sys/fs/cgroup/memory/memory.usage_in_bytes", "/sys/fs/cgroup/memory/memory.stat",
                      "/sys/fs/cgroup/memory/memory.limit_in_bytes")
    KEYS = (b"total_inactive_file ", b"total_active_file ")
else:
    CUR = None

def limit():
    """This container's RAM allotment: OOM_LIMIT_BYTES > RAM_LIMIT_MB > cgroup limit > 512 MB. Never the host's size."""
    if FORCE_LIMIT: return FORCE_LIMIT
    try:
        env = float(os.environ.get("RAM_LIMIT_MB") or 0)
        if env: return int(env * 1048576)
    except ValueError: pass
    try:
        v = int(rd(MAX))
        if 0 < v < 1 << 60: return v
    except Exception: pass
    return 512 * 1048576

if CUR:
    fc, fs = os.open(CUR, os.O_RDONLY), os.open(STAT, os.O_RDONLY)
    def used():
        cur = int(os.pread(fc, 64, 0))
        for line in os.pread(fs, 32768, 0).split(b"\n"):
            for k in KEYS:
                if line.startswith(k): cur -= int(line[len(k):])
        return cur
else:
    def used():                      # no cgroup visible: sum of this container's own processes (PID namespace)
        tot = 0
        for p in os.listdir("/proc"):
            if p.isdigit():
                st = rd(f"/proc/{p}/statm")
                if st:
                    try: tot += int(st.split()[1]) * PAGE
                    except Exception: pass
        return tot

def ppid(pid):
    s = rd(f"/proc/{pid}/stat")
    try: return int(s[s.rindex(")") + 2:].split()[1])
    except Exception: return 0

def comm(pid): return (rd(f"/proc/{pid}/comm") or "").strip()
def cmd(pid): return (rd(f"/proc/{pid}/cmdline") or "").replace("\0", " ").strip()

def protected(pid, cache={}):
    if pid in (SELF, 1): return True
    c = cmd(pid)
    return comm(pid) in PROTECT_NAMES or any(x in c for x in PROTECT_CMD)

def ancestors(pid):
    out, n = [], 0
    while pid > 1 and n < 12:
        out.append(pid); pid = ppid(pid); n += 1
    return out

def biggest():
    cands = []
    for p in os.listdir("/proc"):
        if p.isdigit():
            s = rd(f"/proc/{p}/statm")
            if s:
                try: cands.append((int(s.split()[1]) * PAGE, int(p)))
                except Exception: pass
    cands.sort(reverse=True)
    prot = set(ancestors(SELF))
    for rss, pid in cands:
        if rss and pid not in prot and not protected(pid):
            return pid, rss
    return None, 0

def notify(pid, msg):
    data = (msg + "\r\n").encode()
    for p in ancestors(pid):
        if p != pid and protected(p): continue
        for fd in (2, 1):
            try: tgt = os.readlink(f"/proc/{p}/fd/{fd}")
            except Exception: continue
            if tgt.startswith("pipe:") or tgt.startswith("/dev/pts/") or tgt == "/dev/tty":
                try:
                    h = os.open(f"/proc/{p}/fd/{fd}", os.O_WRONLY | os.O_NONBLOCK | os.O_NOCTTY)
                    try: os.write(h, data)
                    finally: os.close(h)
                    return True
                except Exception: pass
    return False

def record(entry):
    try:
        old = json.loads(rd(STATE) or "[]")
    except Exception: old = []
    old = ([entry] + old)[:10]
    try:
        with open(STATE + ".tmp", "w") as f: json.dump(old, f)
        os.replace(STATE + ".tmp", STATE)
        with open(LOG, "a") as f: f.write(json.dumps(entry) + "\n")
    except Exception: pass

def mb(b): return f"{b / 1048576:.0f} MB"

def main():
    for path, val in (("/proc/self/oom_score_adj", "-1000"),):
        try: open(path, "w").write(val)
        except Exception: pass
    try: os.nice(-10)
    except Exception: pass
    try: open(PIDFILE, "w").write(str(SELF))
    except Exception: pass
    lim, tick, recent = limit(), 0, {}
    trigger = lambda: lim * (1 - FREE_PCT / 100)
    print(f"oomguard: limit {mb(lim)}, kill when free < {FREE_PCT}% (used > {mb(trigger())}), poll {POLL*1000:.0f}ms", flush=True)
    while True:
        tick += 1
        if tick % 200 == 0: lim = limit()
        try: u = used()
        except Exception: time.sleep(1); continue
        if u > trigger():
            pid, rss = biggest()
            if pid and recent.get(pid, 0) < time.time() - 2:
                name, c = comm(pid), cmd(pid)
                free = max(lim - u, 0)
                msg = (f"[OOMGUARD] RAM almost full: only {mb(free)} ({free / lim * 100:.1f}%) free of {mb(lim)}. "
                       f"Killed PID {pid} ({name}, using {mb(rss)}): {c[:200]} . "
                       f"Your command was stopped to keep the server alive (exit code 137). Use less memory or run it in smaller pieces.")
                ok = notify(pid, msg)
                try: os.kill(pid, signal.SIGKILL)
                except Exception: pass
                recent[pid] = time.time()
                record({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "pid": pid, "name": name, "cmd": c[:200],
                        "rss": rss, "free_pct": round(free / lim * 100, 1), "notified": ok})
                print(msg, flush=True)
                time.sleep(0.15)
                continue
        time.sleep(POLL)

if __name__ == "__main__":
    main()
