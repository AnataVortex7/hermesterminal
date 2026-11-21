#!/usr/bin/env python3
"""Optional rclone backup / restore. Does NOTHING (exits quietly) unless BACKUP_REMOTE is set. Every failure is logged and skipped.

  BACKUP_REMOTE   rclone destination, e.g. gdrive:hermes  or  r2:mybucket/hermes     (required to enable)
  RCLONE_CONF_B64 base64 of your rclone.conf (`base64 -w0 ~/.config/rclone/rclone.conf`)  - or use native RCLONE_CONFIG_<REMOTE>_* env vars
  BACKUP_NAME     sub-folder on the remote (default TAILSCALE_HOSTNAME / hermes-cloud) so several boxes can share one remote
  BACKUP_INTERVAL seconds between checks (default 300 = 5 min)
  BACKUP_PATHS    space/comma separated (default: /root /home /opt /srv /usr/local /var/www)  - missing paths are skipped
  BACKUP_EXCLUDES extra exclude patterns (fnmatch on the full path; a pattern without "/" matches the file name)
  BACKUP_ZSTD_LEVEL (default 3)   BACKUP_APT=0 (skip apt package list)   BACKUP_ENABLED=0 (turn off)   BACKUP_FORCE_RESTORE=1
  BACKUP_STATE_DIR (default /var/lib/hermes-backup)

How it works
  * The paths are split into "units" = every direct child directory of each path (+ one unit for the loose files of that path).
  * Every cycle each unit is fingerprinted (path/mode/owner/size/mtime). ONLY units whose fingerprint changed are re-packed
    (tar + zstd, owners/permissions/symlinks kept) and uploaded with rclone. Unchanged units are not touched.
  * A unit that disappeared locally (you deleted it over ssh) is deleted from the remote in the same cycle; a file deleted inside a unit
    simply is not in the re-packed archive. So the backup mirrors the box, deletes included.
  * apt packages you installed (apt-mark showmanual minus the packages of the base image) are saved as a list and re-installed on restore.
    Packages installed with pip/npm -g/cargo/etc. live under /usr/local, /root, /opt, so they come back with the files.
  * Restore (on a fresh box, i.e. no state marker): download + extract every unit, then apt-get install the missing packages.
    Backups only start AFTER the restore finished, so a failed restore can never overwrite a good backup with an empty box.
"""
import os, sys, json, time, hashlib, subprocess, shutil, fnmatch, stat, base64

E = os.environ.get
REMOTE = (E("BACKUP_REMOTE") or "").strip().rstrip("/")
NAME = (E("BACKUP_NAME") or E("TAILSCALE_HOSTNAME") or "hermes-cloud").strip("/ ")
try:
    INTERVAL = max(30, int(float(E("BACKUP_INTERVAL") or 300)))
except ValueError:
    INTERVAL = 300
PATHS = [os.path.normpath(p) for p in (E("BACKUP_PATHS") or "/root /home /opt /srv /usr/local /var/www").replace(",", " ").split() if p]
EXCLUDES = ["/root/.cache", "/root/.config/rclone", "/root/.npm/_cacache", "/root/.npm/_logs", "/root/.local/share/Trash",
            "/usr/local/bin/tailscale", "/usr/local/bin/tailscaled",
            "/usr/local/bin/free", "/usr/local/bin/nproc", "/usr/local/bin/uptime",
            "/usr/local/bin/top", "/usr/local/bin/df", "/usr/local/bin/lscpu",    # symlinks to cgshim.py, owned by the image
            "__pycache__", "*.pyc"]
EXCLUDES += [x for x in (E("BACKUP_EXCLUDES") or "").replace(",", " ").split() if x]
LEVEL = E("BACKUP_ZSTD_LEVEL") or "3"
STATE = E("BACKUP_STATE_DIR") or "/var/lib/hermes-backup"
LOG = E("BACKUP_LOG") or "/var/log/hermes-backup.log"
STATUS = E("BACKUP_STATUS") or "/run/hermes-backup.json"
BASE_MANUAL = "/usr/share/hermes-base-manual.txt"          # written by the Dockerfile
USE_APT = (E("BACKUP_APT", "1") or "1").lower() not in ("0", "false", "no", "off")
BASE = f"{REMOTE}/{NAME}"
UNITS, META = BASE + "/units", BASE + "/meta"
ZSTD = shutil.which("zstd") is not None
EXT = ".tar.zst" if ZSTD else ".tar.gz"
NICE = ["nice", "-n", "19"] + (["ionice", "-c3"] if shutil.which("ionice") else [])

st = {"state": "starting", "remote": REMOTE, "last_ok": None, "last_error": None, "units": 0, "uploaded": 0,
      "bytes": 0, "restored": None, "pending": [], "next": None}


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 1_000_000:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def status(**kw):
    st.update(kw)
    try:
        tmp = STATUS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, STATUS)
    except Exception:
        pass


# ---------------- rclone helpers ----------------
def rc_cmd(*args):
    return ["rclone", *args, "--contimeout", "20s", "--timeout", "120s", "--retries", "2", "--low-level-retries", "3"]


def rclone(*args, timeout=1800):
    try:
        r = subprocess.run(NICE + rc_cmd(*args), capture_output=True, timeout=timeout)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
    except Exception as e:
        return 99, "", str(e)


def setup_rclone():
    b64 = E("RCLONE_CONF_B64")
    if b64:
        os.makedirs(STATE, exist_ok=True)
        cfg = os.path.join(STATE, "rclone.conf")
        with open(cfg, "wb") as f:
            f.write(base64.b64decode(b64))
        os.chmod(cfg, 0o600)
        os.environ["RCLONE_CONFIG"] = cfg


def list_units():
    """names on the remote, [] if the folder does not exist yet, None if the remote could not be reached"""
    rc, out, err = rclone("lsf", "--files-only", UNITS, timeout=300)
    if rc == 0:
        return [l for l in out.splitlines() if l]
    if rc == 3 or "directory not found" in err.lower():
        return []
    log(f"remote not reachable: {err.strip()[-300:]}")
    return None


def cat_json(path, default):
    rc, out, _ = rclone("cat", path, timeout=120)
    try:
        return json.loads(out) if rc == 0 else default
    except Exception:
        return default


def put_text(local_text, remote_path):
    tmp = os.path.join(STATE, "meta.tmp")
    with open(tmp, "w") as f:
        f.write(local_text)
    rc, _, err = rclone("copyto", tmp, remote_path, timeout=300)
    os.unlink(tmp)
    if rc != 0:
        log(f"meta upload failed ({remote_path}): {err.strip()[-200:]}")
    return rc == 0


# ---------------- units / fingerprints ----------------
def excluded(p):
    base = os.path.basename(p)
    for pat in EXCLUDES:
        if fnmatch.fnmatchcase(base if "/" not in pat else p, pat):
            return True
    return False


def safe(path):
    return path.strip("/").replace("/", "_")[:70] + "-" + hashlib.md5(path.encode()).hexdigest()[:8]


def enumerate_units():
    units, seen = [], set()
    for base in PATHS:
        if not os.path.isdir(base) or os.path.islink(base):
            continue
        has_files = False
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for n in names:
            p = os.path.join(base, n)
            if excluded(p):
                continue
            try:
                s = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISDIR(s.st_mode):
                units.append({"name": safe(p) + EXT, "kind": "dir", "path": p})
            elif stat.S_ISREG(s.st_mode) or stat.S_ISLNK(s.st_mode):
                has_files = True
        if has_files:
            units.append({"name": safe(base) + "-files" + EXT, "kind": "files", "path": base})
    out = []
    for u in units:
        if u["name"] not in seen:
            seen.add(u["name"])
            out.append(u)
    return out


def walk_unit(u):
    if u["kind"] == "files":
        for n in sorted(os.listdir(u["path"])):
            p = os.path.join(u["path"], n)
            if excluded(p):
                continue
            try:
                s = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISREG(s.st_mode) or stat.S_ISLNK(s.st_mode):
                yield p, s
        return
    top = u["path"]
    s0 = os.lstat(top)
    yield top, s0
    dev = s0.st_dev
    for root, dirs, files in os.walk(top, followlinks=False):
        keep = []
        for d in sorted(dirs):
            p = os.path.join(root, d)
            if excluded(p):
                continue
            try:
                s = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISLNK(s.st_mode):
                yield p, s
            elif s.st_dev == dev:                    # never cross into another filesystem / mount
                yield p, s
                keep.append(d)
        dirs[:] = keep
        for f in sorted(files):
            p = os.path.join(root, f)
            if excluded(p):
                continue
            try:
                s = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISREG(s.st_mode) or stat.S_ISLNK(s.st_mode):
                yield p, s


def fingerprint(u, listfile):
    h = hashlib.sha1()
    n = 0
    with open(listfile, "wb") as lf:
        for p, s in walk_unit(u):
            if stat.S_ISLNK(s.st_mode):
                try:
                    extra = os.readlink(p)
                except OSError:
                    extra = ""
            elif stat.S_ISREG(s.st_mode):
                extra = f"{s.st_size}:{int(s.st_mtime)}"      # whole seconds: tar keeps seconds, so a restored box matches
            else:
                extra = ""
            h.update(f"{p}\0{s.st_mode:o}\0{s.st_uid}\0{s.st_gid}\0{extra}\n".encode("utf-8", "surrogateescape"))
            lf.write(p.lstrip("/").encode("utf-8", "surrogateescape") + b"\0")
            n += 1
    return h.hexdigest(), n


def make_archive(listfile, out):
    comp = ["zstd", "-q", f"-{LEVEL}", "-T1"] if ZSTD else ["gzip", "-n", "-6"]
    errf = open(out + ".err", "wb")
    with open(out, "wb") as of:
        t = subprocess.Popen(NICE + ["tar", "--null", "--no-recursion", "--numeric-owner", "-C", "/", "-T", listfile, "-cpf", "-"],
                             stdout=subprocess.PIPE, stderr=errf)
        c = subprocess.Popen(NICE + comp, stdin=t.stdout, stdout=of, stderr=errf)
        t.stdout.close()
        c.wait()
        t.wait()
    errf.close()
    os.unlink(out + ".err")
    return t.returncode in (0, 1) and c.returncode == 0       # tar rc 1 = "file changed while reading": fine for a live box


# ---------------- apt package list ----------------
def apt_extra():
    """manually installed apt packages that are not part of the base image"""
    try:
        cur = set(subprocess.run(["apt-mark", "showmanual"], capture_output=True, text=True, timeout=60).stdout.split())
    except Exception:
        return None
    try:
        base = set(open(BASE_MANUAL).read().split())
    except Exception:
        base = set()
    return sorted(cur - base)


def apt_installed():
    try:
        out = subprocess.run(["dpkg-query", "-W", "-f", "${Package} ${Status}\n"], capture_output=True, text=True, timeout=60).stdout
        return {l.split()[0] for l in out.splitlines() if l.endswith("install ok installed")}
    except Exception:
        return set()


APT_FAILED = {}          # package -> tries; kept in the saved list for 3 tries so a flaky mirror does not erase it


def apt_restore(pkgs):
    missing = [p for p in pkgs if p not in apt_installed()]
    if not missing or not shutil.which("apt-get"):
        return
    log(f"apt: installing {len(missing)} package(s): {' '.join(missing)[:200]}")
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    run = lambda a, t=1800: subprocess.run(NICE + a, env=env, capture_output=True, timeout=t)
    try:
        run(["apt-get", "update"], 300)
        r = run(["apt-get", "install", "-y", "--no-install-recommends", *missing])
        if r.returncode != 0:
            log("apt: batch install failed, trying one by one")
            for p in missing:
                if run(["apt-get", "install", "-y", "--no-install-recommends", p]).returncode != 0:
                    APT_FAILED[p] = APT_FAILED.get(p, 0) + 1
                    log(f"apt: skipped {p}")
    except Exception as e:
        log(f"apt: error, skipped ({e})")
        for p in missing:
            APT_FAILED[p] = APT_FAILED.get(p, 0) + 1


# ---------------- restore ----------------
def restore_unit(name):
    dec = ["zstd", "-dc"] if name.endswith(".zst") else ["gzip", "-dc"]
    try:
        p1 = subprocess.Popen(NICE + rc_cmd("cat", f"{UNITS}/{name}"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        p2 = subprocess.Popen(dec, stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        p1.stdout.close()
        p3 = subprocess.Popen(["tar", "-C", "/", "-xpf", "-", "--numeric-owner"], stdin=p2.stdout, stderr=subprocess.PIPE)
        p2.stdout.close()
        err = p3.communicate(timeout=3600)[1].decode("utf-8", "replace")
        p2.wait()
        p1.wait()
        if err.strip():
            log(f"restore {name}: tar said: {err.strip()[-200:]}")
        return p1.returncode == 0 and p2.returncode == 0      # download+decompress intact (tar warnings are tolerated)
    except Exception as e:
        log(f"restore {name} failed: {e}")
        return False


TRIES = {}


def do_restore(only=None):
    """-> True when finished (even with skipped units), False when the remote is unreachable (retry next cycle)"""
    names = list_units()
    if names is None:
        return False
    if only is None:
        status(state="restoring")
        if not names:
            log("restore: remote is empty -> fresh start")
        else:
            log(f"restore: {len(names)} unit(s) from {BASE}")
    todo = [n for n in sorted(names) if only is None or n in only]
    for n in todo:
        if restore_unit(n):
            PENDING.discard(n)
        else:
            TRIES[n] = TRIES.get(n, 0) + 1
            if TRIES[n] >= 3:
                log(f"restore: giving up on {n} after 3 tries (skipped)")
                PENDING.discard(n)
            else:
                PENDING.add(n)
    if only is None:
        rh = cat_json(f"{META}/hashes.json", {})
        HASHES.clear()
        HASHES.update({k: v for k, v in rh.items() if k in names and k not in PENDING})
        save_hashes()
        if USE_APT:
            pk = cat_json(f"{META}/apt-manual.json", [])
            if pk:
                apt_restore(pk)
        try:
            os.sync()
        except Exception:
            pass
        os.makedirs(STATE, exist_ok=True)
        open(os.path.join(STATE, "restored"), "w").write(time.strftime("%F %T"))
        status(restored=time.strftime("%F %T"))
        log("restore: done")
    return True


# ---------------- backup ----------------
HASHES, PENDING = {}, set()


def save_hashes():
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "hashes.json"), "w") as f:
            json.dump(HASHES, f)
    except Exception:
        pass


def backup_cycle():
    status(state="backing-up")
    tmpd = os.path.join(STATE, "tmp")
    shutil.rmtree(tmpd, ignore_errors=True)
    os.makedirs(tmpd, exist_ok=True)
    units = enumerate_units()
    names = {u["name"] for u in units}
    up = bytes_ = 0
    errors = 0
    for u in units:
        if u["name"] in PENDING:
            continue                                  # not restored yet: never overwrite the remote copy with a partial local one
        lst = os.path.join(tmpd, "list")
        try:
            h, n = fingerprint(u, lst)
        except Exception as e:
            log(f"skip {u['path']}: {e}")
            continue
        if HASHES.get(u["name"]) == h or n == 0:
            continue
        arc = os.path.join(tmpd, "unit" + EXT)
        try:
            if not make_archive(lst, arc):
                raise RuntimeError("tar/compress failed")
            size = os.path.getsize(arc)
            rc, _, err = rclone("copyto", arc, f"{UNITS}/{u['name']}")
            if rc != 0:
                raise RuntimeError(err.strip()[-200:])
            HASHES[u["name"]] = h
            up += 1
            bytes_ += size
            log(f"uploaded {u['path']} ({n} entries, {size // 1024} KB)")
        except Exception as e:
            errors += 1
            log(f"backup of {u['path']} skipped: {e}")
        finally:
            for f in (arc, arc + ".err"):
                try:
                    os.unlink(f)
                except OSError:
                    pass
    # units that no longer exist on the box -> delete from the remote too
    remote = list_units()
    if remote is not None and not PENDING:
        stale = [r for r in remote if r not in names]
        if stale and not names:
            log("refusing to delete: nothing found locally (looks like a broken mount)")
        else:
            for r in stale:
                rc, _, err = rclone("deletefile", f"{UNITS}/{r}", timeout=300)
                log(f"deleted from remote: {r}" if rc == 0 else f"delete {r} failed: {err.strip()[-150:]}")
                HASHES.pop(r, None)
    for k in [k for k in HASHES if k not in names]:
        HASHES.pop(k, None)
    save_hashes()
    if up or errors == 0:
        old = cat_json(f"{META}/hashes.json", None)
        if old != HASHES:
            put_text(json.dumps(HASHES), f"{META}/hashes.json")
    if USE_APT:
        extra = apt_extra()
        if extra is not None:
            for p, t in list(APT_FAILED.items()):          # retry packages that failed to install, max 3 times
                if p in apt_installed():
                    APT_FAILED.pop(p)
                elif t < 3:
                    apt_restore([p])
            keep = sorted(set(extra) | {p for p, t in APT_FAILED.items() if t < 3})
            if cat_json(f"{META}/apt-manual.json", None) != keep:
                put_text(json.dumps(keep), f"{META}/apt-manual.json")
    shutil.rmtree(tmpd, ignore_errors=True)
    status(state="ok" if errors == 0 else "partial", units=len(units), uploaded=up, bytes=bytes_,
           last_ok=time.strftime("%F %T") if errors == 0 else st["last_ok"], pending=sorted(PENDING),
           last_error=None if errors == 0 else f"{errors} unit(s) skipped, see {LOG}")


def main():
    if not REMOTE or (E("BACKUP_ENABLED", "1") or "1").lower() in ("0", "false", "no", "off"):
        print("backup: BACKUP_REMOTE not set (or BACKUP_ENABLED=0) -> disabled", flush=True)
        status(state="disabled")
        return
    if not shutil.which("rclone"):
        log("backup: rclone not installed -> skipped")
        status(state="disabled", last_error="rclone not installed")
        return
    setup_rclone()
    os.makedirs(STATE, exist_ok=True)
    try:
        os.nice(5)
    except Exception:
        pass
    marker = os.path.join(STATE, "restored")
    restored = os.path.exists(marker) and not E("BACKUP_FORCE_RESTORE")
    if restored:                                   # box kept its disk: local state is the truth, just continue
        try:
            HASHES.update(json.load(open(os.path.join(STATE, "hashes.json"))))
        except Exception:
            pass
        log("backup: state marker found, skipping restore")
    log(f"backup: remote={BASE} every {INTERVAL}s paths={' '.join(PATHS)} compression={'zstd' if ZSTD else 'gzip'}")
    while True:
        try:
            if not restored:
                restored = do_restore()
                if not restored:
                    status(state="error", last_error="remote not reachable, restore retried next cycle (backup is paused)")
            elif PENDING:
                do_restore(only=set(PENDING))
            if restored:
                backup_cycle()
        except Exception as e:
            log(f"cycle error (skipped): {e}")
            status(state="error", last_error=str(e)[:200])
        status(next=time.strftime("%F %T", time.localtime(time.time() + INTERVAL)))
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
