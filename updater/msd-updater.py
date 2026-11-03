#!/usr/bin/env python3
"""Update service for the Media Server Dashboard (runs as root, standard library + cosign).

Installs only releases that the release workflow of the configured GitHub repository signed (Sigstore
keyless, bound to that workflow at the release tag), never an older version, and only what the app asked
for. The app (user msd) can't do any of this itself: it writes a request file, systemd starts this script
(msd-updater.path), and the app reads the status this script publishes.

    msd-updater.py run-request    handle the app's request file (started by msd-updater.path)
    msd-updater.py check          look for a newer release and verify it (daily timer)
    msd-updater.py install X.Y.Z  install a release by hand
    msd-updater.py status         print the status

An install: verify release.json and its signature, check the release policy, download the archive and
check its SHA-256, back up the settings, unpack next to the running version, switch the `current`
symlink, restart, wait until the new version answers. If it doesn't, the previous version is switched
back and restarted.
"""
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import stat
import sys
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

UPDATER_VERSION = "1.3.1"
CONF_FILE = os.environ.get("MSD_UPDATER_CONF") or "/etc/media-server-dashboard/updater.json"
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
ISSUER = "https://token.actions.githubusercontent.com"
STEPS = ["Check release", "Check signature", "Download", "Back up", "Unpack", "Switch over", "Restart", "Health check", "Clean up"]


class Refused(Exception):
    """The release may not be installed (signature, policy, version). Nothing was changed."""


class Failed(Exception):
    """Something went wrong while installing."""


def vtuple(v):
    m = VERSION_RE.match(v or "")
    if not m:
        raise ValueError(f"not a version: {v!r}")
    return tuple(int(x) for x in m.groups())


def satisfies(version, spec):
    """Tiny version ranges: '>=1.0.0', '>=1.0.0 <2.0.0', or empty."""
    for part in (spec or "").split():
        m = re.match(r"^(>=|<=|>|<|==)?(\d+\.\d+\.\d+)$", part)
        if not m:
            raise ValueError(f"unsupported version range: {spec!r}")
        op, v = m[1] or "==", vtuple(m[2])
        cur = vtuple(version)
        ok = {">=": cur >= v, "<=": cur <= v, ">": cur > v, "<": cur < v, "==": cur == v}[op]
        if not ok:
            return False
    return True


class Updater:
    def __init__(self, conf):
        self.c = {
            "channel": "stable", "workflow": ".github/workflows/release.yml", "api": "https://api.github.com",
            "app_root": "/opt/media-server-dashboard", "data_dir": "/var/lib/media-server-dashboard",
            "work_dir": "/var/lib/media-server-dashboard-updater", "service": "media-server-dashboard",
            "health_url": "http://127.0.0.1:8900/healthz", "cosign": "/usr/local/lib/media-server-dashboard/cosign",
            "systemctl": "systemctl", "keep_releases": 3, "keep_backups": 3, "health_timeout": 60, **conf,
        }
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.c.get("repo") or ""):
            raise SystemExit("updater.json: repo must be owner/name")
        self.status_file = self.c.get("status_file") or os.path.join(self.c["work_dir"], "public", "status.json")
        self.request_file = os.path.join(self.c["data_dir"], "updater", "request.json")
        self.status = self._read_status()

    # ---------- status, published for the app (root-owned dir, world-readable file) ----------
    def _read_status(self):
        try:
            with open(self.status_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"state": "idle", "log": []}

    def save(self):
        os.makedirs(os.path.dirname(self.status_file), mode=0o755, exist_ok=True)
        tmp = f"{self.status_file}.{os.getpid()}.tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644), "w", encoding="utf-8") as f:
            json.dump(self.status, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.status_file)

    def log(self, text):
        line = f"{datetime.now().strftime('%H:%M:%S')} {text}"
        print(line, flush=True)
        self.status.setdefault("log", []).append(line)
        self.status["log"] = self.status["log"][-80:]
        self.save()

    def step(self, name, state="running"):
        steps = self.status.setdefault("steps", [])
        for s in steps:
            if s["name"] == name:
                s.update(state=state, at=int(time.time()))
                break
        else:
            steps.append({"name": name, "state": state, "at": int(time.time())})
        self.status["step"] = name
        self.save()

    # ---------- GitHub ----------
    def _get(self, url, accept="application/vnd.github+json", limit=50_000_000):
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"msd-updater/{UPDATER_VERSION}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(limit + 1)
        if len(data) > limit:
            raise Failed(f"download too large: {url}")
        return data

    def repo(self):
        """The repository's current name. GitHub forwards a moved or renamed repository (same repository, new
        owner or name), so its API names the one whose release workflow signs from now on."""
        if not getattr(self, "_repo", None):
            self._repo = self.c["repo"]
            try:
                name = json.loads(self._get(f"{self.c['api']}/repos/{self.c['repo']}")).get("full_name") or ""
                if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
                    if name.lower() != self.c["repo"].lower():
                        self.log(f"The repository moved: {self.c['repo']} → {name}")
                    self._repo = name
            except (OSError, ValueError, urllib.error.URLError):
                pass
        return self._repo

    def releases(self):
        data = json.loads(self._get(f"{self.c['api']}/repos/{self.repo()}/releases?per_page=30"))
        out = []
        for r in data:
            tag = r.get("tag_name") or ""
            if r.get("draft") or not tag.startswith("v") or not VERSION_RE.match(tag[1:]):
                continue
            if r.get("prerelease") and self.c["channel"] == "stable":
                continue
            out.append(r)
        return sorted(out, key=lambda r: vtuple(r["tag_name"][1:]), reverse=True)

    def release(self, version):
        return json.loads(self._get(f"{self.c['api']}/repos/{self.repo()}/releases/tags/v{version}"))

    def asset(self, rel, name, limit=50_000_000):
        for a in rel.get("assets") or []:
            if a.get("name") == name:
                return self._get(a["browser_download_url"], accept="application/octet-stream", limit=limit)
        raise Refused(f"release {rel.get('tag_name')} has no file {name}")

    # ---------- trust ----------
    def verify(self, version, doc, bundle, workdir):
        """Sigstore keyless: release.json must be signed by this repository's release workflow at the tag.
        cosign reads downloaded files, so as root it runs as an unprivileged user in a folder of its own."""
        workdir = os.path.join(workdir, "verify")
        shutil.rmtree(workdir, ignore_errors=True)
        os.makedirs(workdir, mode=0o700)
        d, b = os.path.join(workdir, "release.json"), os.path.join(workdir, "release.json.sigstore.json")
        home = os.path.join(workdir, "home")  # cosign keeps Sigstore's trust root here
        os.makedirs(home, mode=0o700)
        with open(d, "wb") as f:
            f.write(doc)
        with open(b, "wb") as f:
            f.write(bundle)
        tag = f"v{version}"
        repo = self.repo()
        identity = f"https://github.com/{repo}/{self.c['workflow']}@refs/tags/{tag}"
        cmd = [self.c["cosign"], "verify-blob", "--bundle", b,
               "--certificate-identity", identity, "--certificate-oidc-issuer", ISSUER,
               "--certificate-github-workflow-repository", repo,
               "--certificate-github-workflow-ref", f"refs/tags/{tag}",
               "--certificate-github-workflow-trigger", "push", d]
        drop = {}
        user = self.c.get("verify_user", "nobody")
        if user and os.geteuid() == 0:
            import pwd
            pw = pwd.getpwnam(user)
            # The files stay root's (cosign only reads them), only its home is its own. The work folder and
            # the ones above it are root's 0700: let it pass through them, without seeing what is in them.
            self.passable(workdir)
            os.chmod(workdir, 0o755)
            for path in (d, b):
                os.chmod(path, 0o644)
            os.chown(home, pw.pw_uid, pw.pw_gid)
            drop = {"user": pw.pw_uid, "group": pw.pw_gid, "extra_groups": []}
        env = {"PATH": "/usr/bin:/bin", "HOME": home, **{k: v for k, v in os.environ.items() if k in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE")}}
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env, cwd=home, **drop)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Refused(f"cosign could not run: {e}")
        if r.returncode != 0:
            out = (r.stderr or r.stdout).strip().splitlines()
            raise Refused("invalid signature" + (f": {out[-1][:300]}" if out else ""))

    def passable(self, path):
        """Others may pass through (x, not r) the folders from the work folder down to `path`."""
        top = os.path.realpath(self.c["work_dir"])
        p = os.path.realpath(os.path.dirname(path))
        while p == top or p.startswith(top + os.sep):
            st = os.lstat(p)
            if stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o001:
                os.chmod(p, stat.S_IMODE(st.st_mode) | 0o011)
            if p == top:
                break
            p = os.path.dirname(p)

    def evaluate(self, version, doc, current):
        """Read the signed release.json and its upgrade policy. Raises Refused with the reason."""
        try:
            rel = json.loads(doc)
        except ValueError:
            raise Refused("release.json is not JSON")
        if rel.get("version") != version or rel.get("tag") != f"v{version}":
            raise Refused("release.json belongs to another version")
        art = rel.get("artifact") or {}
        if not re.fullmatch(r"[A-Za-z0-9._-]+\.tar\.gz", art.get("name") or "") or not re.fullmatch(r"[0-9a-f]{64}", art.get("sha256") or ""):
            raise Refused("release.json: the archive is missing")
        if vtuple(version) <= vtuple(current):
            raise Refused(f"{version} is not newer than {current}")
        pol = rel.get("policy") or {}
        if pol.get("minimumFromVersion") and vtuple(current) < vtuple(pol["minimumFromVersion"]):
            raise Refused(f"update to {pol['minimumFromVersion']} or newer first")
        if pol.get("requiresUpdater") and not satisfies(UPDATER_VERSION, pol["requiresUpdater"]):
            raise Refused(f"needs a newer update service ({pol['requiresUpdater']}): run install-lxc.sh --update")
        manual = pol.get("manualSteps") or {}
        if manual.get("required"):
            raise Refused("manual steps needed: " + (manual.get("summary") or "") + (f" ({manual['url']})" if manual.get("url") else ""))
        return rel

    # ---------- the installed versions ----------
    def current_version(self):
        try:
            with open(os.path.join(self.c["app_root"], "current", "VERSION"), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return "0.0.0"

    def current_target(self):
        try:
            return os.path.realpath(os.path.join(self.c["app_root"], "current"))
        except OSError:
            return None

    def switch(self, target):
        link = os.path.join(self.c["app_root"], "current")
        tmp = link + ".new"
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        os.symlink(target, tmp)
        os.replace(tmp, link)

    def restart(self):
        r = subprocess.run([self.c["systemctl"], "restart", self.c["service"]], capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            raise Failed(f"restart failed: {r.stderr.strip()[:300]}")

    def healthy(self, version):
        deadline = time.time() + self.c["health_timeout"]
        last = "no answer"
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(self.c["health_url"], timeout=5) as r:
                    got = json.load(r).get("version")
                if got == version:
                    return True
                last = f"reports version {got}"
            except (OSError, ValueError) as e:
                last = str(e)
            time.sleep(1.5)
        raise Failed(f"health check: {last}")

    @staticmethod
    def extract(archive, dest, version):
        """Unpack the release archive safely: plain files and folders only, nothing outside `dest`."""
        prefix = f"media-server-dashboard-{version}/"
        with tarfile.open(archive, "r:gz") as tar:
            for m in tar.getmembers():
                name = m.name
                if not name.startswith(prefix) and name != prefix.rstrip("/"):
                    raise Failed(f"unexpected path in the archive: {name}")
                rel = name[len(prefix):]
                if not rel:
                    continue
                if rel.startswith("/") or ".." in rel.split("/") or "\\" in rel:
                    raise Failed(f"unsafe path in the archive: {name}")
                path = os.path.join(dest, rel)
                if m.isdir():
                    os.makedirs(path, mode=0o755, exist_ok=True)
                elif m.isfile():
                    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
                    with tar.extractfile(m) as src, open(path, "wb") as out:
                        shutil.copyfileobj(src, out)
                    os.chmod(path, 0o755 if m.mode & 0o111 else 0o644)
                elif m.issym() or m.islnk():
                    raise Failed(f"links in the archive are not allowed: {name}")
                else:
                    raise Failed(f"unexpected entry in the archive: {name}")

    def backup(self, version):
        """The app's settings and game, before anything changes. The last few are kept."""
        bdir = os.path.join(self.c["work_dir"], "backups")
        os.makedirs(bdir, mode=0o700, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(bdir, f"{stamp}-{version}.tar.gz")
        mask = os.umask(0o077)
        try:
            with tarfile.open(path, "w:gz") as tar:
                for name in ("config.json", ".secret", "game.json", "events.json"):
                    p = os.path.join(self.c["data_dir"], name)
                    if os.path.isfile(p) and not os.path.islink(p):
                        tar.add(p, arcname=name)
        finally:
            os.umask(mask)
        old = sorted(os.listdir(bdir))
        for f in old[:-self.c["keep_backups"]]:
            os.remove(os.path.join(bdir, f))
        return path

    def self_update(self, release_dir):
        """After a verified, healthy update: take the update service from that release if it is newer.
        It comes from the same signed archive as the app, so it is trusted the same way."""
        new = os.path.join(release_dir, "updater", "msd-updater.py")
        me = os.path.realpath(self.c.get("self_path") or __file__)
        try:
            text = open(new, encoding="utf-8").read()
            m = re.search(r'^UPDATER_VERSION = "(\d+\.\d+\.\d+)"', text, re.M)
            if not m or vtuple(m[1]) <= vtuple(UPDATER_VERSION) or not os.access(os.path.dirname(me), os.W_OK):
                return
            tmp = me + ".new"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(tmp, 0o755)
            os.replace(tmp, me)
            self.log(f"Update service updated to {m[1]}")
        except (OSError, ValueError) as e:
            self.log(f"Update service stays at {UPDATER_VERSION}: {e}")

    def prune(self):
        rdir = os.path.join(self.c["app_root"], "releases")
        cur = self.current_target()
        found = []
        for name in os.listdir(rdir):
            p = os.path.join(rdir, name)
            if VERSION_RE.match(name.split("-")[0]) and os.path.isdir(p) and os.path.realpath(p) != cur:
                found.append((os.path.getmtime(p), p))
        for _, p in sorted(found, reverse=True)[self.c["keep_releases"] - 1:]:
            shutil.rmtree(p, ignore_errors=True)

    # ---------- actions ----------
    def check(self, requested_by="schedule"):
        current = self.current_version()
        self.status.update(state="checking", checked_by=requested_by, steps=[], step=None)
        self.save()
        refused, available = [], None
        try:
            for rel in self.releases():
                version = rel["tag_name"][1:]
                if vtuple(version) <= vtuple(current):
                    break
                try:
                    doc = self.asset(rel, "release.json", 1_000_000)
                    bundle = self.asset(rel, "release.json.sigstore.json", 1_000_000)
                    self.verify(version, doc, bundle, os.path.join(self.c["work_dir"], "check"))
                    meta = self.evaluate(version, doc, current)
                    available = {"version": version, "name": rel.get("name") or rel["tag_name"], "url": rel.get("html_url"),
                                 "notes": (meta.get("notes") or rel.get("body") or "")[:6000], "published": rel.get("published_at"),
                                 "verified": True, "installable": True}
                    break
                except Refused as e:
                    refused.append({"version": version, "reason": str(e)})
        except (OSError, ValueError, urllib.error.URLError) as e:
            self.status.update(state="idle", check_error=f"{type(e).__name__}: {e}"[:300])
            self.save()
            return
        self.status.update(state="idle", available=available, refused=refused[:5], checked_at=int(time.time()), current=current, check_error=None)
        self.save()

    def install(self, version, requested_by="cli"):
        vtuple(version)
        current = self.current_version()
        previous = self.current_target()
        self.status = {"state": "running", "id": f"{int(time.time())}-{version}", "from": current, "to": version,
                       "requested_by": requested_by, "started": int(time.time()), "steps": [{"name": s, "state": "pending"} for s in STEPS],
                       "log": [], "available": self.status.get("available"), "refused": self.status.get("refused", []),
                       "last": self.status.get("last"), "current": current}
        self.log(f"Update {current} → {version} (requested by {requested_by})")
        work = os.path.join(self.c["work_dir"], "work", version)
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, mode=0o700)
        switched = False
        try:
            self.step("Check release")
            rel = self.release(version)
            doc = self.asset(rel, "release.json", 1_000_000)
            bundle = self.asset(rel, "release.json.sigstore.json", 1_000_000)
            self.step("Check release", "done")
            self.step("Check signature")
            self.verify(version, doc, bundle, work)
            meta = self.evaluate(version, doc, current)
            self.log("Signature valid: release.json comes from the release workflow")
            self.step("Check signature", "done")

            self.step("Download")
            art = meta["artifact"]
            data = self.asset(rel, art["name"])
            digest = hashlib.sha256(data).hexdigest()
            if digest != art["sha256"]:
                raise Refused(f"the archive's SHA-256 does not match ({digest[:12]}… instead of {art['sha256'][:12]}…)")
            archive = os.path.join(work, art["name"])
            with open(archive, "wb") as f:
                f.write(data)
            self.log(f"{art['name']} downloaded ({len(data) // 1024} KB), SHA-256 matches")
            self.step("Download", "done")

            self.step("Back up")
            self.log(f"Backup: {self.backup(current)}")
            self.step("Back up", "done")

            self.step("Unpack")
            rdir = os.path.join(self.c["app_root"], "releases")
            os.makedirs(rdir, mode=0o755, exist_ok=True)
            dest = os.path.join(rdir, version)
            staging = dest + ".tmp"
            shutil.rmtree(staging, ignore_errors=True)
            os.makedirs(staging, mode=0o755)
            self.extract(archive, staging, version)
            with open(os.path.join(staging, "VERSION"), encoding="utf-8") as f:
                if f.read().strip() != version:
                    raise Failed("VERSION in the archive does not match")
            if not os.path.isfile(os.path.join(staging, "server.py")):
                raise Failed("server.py is missing from the archive")
            r = subprocess.run([sys.executable, "-m", "py_compile", *[os.path.join(staging, f) for f in os.listdir(staging) if f.endswith(".py")]],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise Failed("the Python files do not compile: " + r.stderr[-300:])
            if os.path.realpath(dest) == previous:
                raise Failed("this version is already running")
            shutil.rmtree(dest, ignore_errors=True)
            os.rename(staging, dest)
            self.step("Unpack", "done")

            self.step("Switch over")
            self.switch(dest)
            switched = True
            self.step("Switch over", "done")
            self.step("Restart")
            self.restart()
            self.step("Restart", "done")
            self.step("Health check")
            self.healthy(version)
            self.log(f"Version {version} is running")
            self.step("Health check", "done")
            self.step("Clean up")
            self.self_update(dest)
            self.prune()
            shutil.rmtree(work, ignore_errors=True)
            self.step("Clean up", "done")
            self.status.update(result="succeeded", finished=int(time.time()), available=None, current=version)
            self.log("Done")
        except Refused as e:
            self.step(self.status.get("step") or STEPS[0], "failed")
            self.status.update(result="refused", error=str(e), finished=int(time.time()))
            self.log(f"Refused: {e}. Nothing was changed.")
        except Exception as e:  # noqa: BLE001 - every failure must end in a known state
            self.step(self.status.get("step") or STEPS[0], "failed")
            msg = f"{type(e).__name__}: {e}" if not isinstance(e, Failed) else str(e)
            self.status.update(error=msg[:500], finished=int(time.time()))
            self.log(f"Error: {msg}")
            if switched and previous:
                self.rollback(previous, current)
            else:
                self.status["result"] = "failed"
                self.log("Nothing was switched over; the previous version keeps running.")
        st = self.status
        st["last"] = {k: st.get(k) for k in ("id", "result", "from", "to", "error", "requested_by", "started", "finished")}
        st["state"] = "attention" if st.get("result") == "attention" else "idle"
        self.save()

    def rollback(self, previous, version):
        self.log(f"Back to {version} …")
        try:
            self.switch(previous)
            self.restart()
            self.healthy(version)
            self.status["result"] = "rolled_back"
            self.log(f"Version {version} is running again.")
        except Exception as e:  # noqa: BLE001
            self.status["result"] = "attention"
            self.log(f"Switching back failed: {e}. Please check by hand: systemctl status {self.c['service']}")

    def run_request(self):
        """The app's request: read it once (no symlinks, small), remove it, do it."""
        try:
            fd = os.open(self.request_file, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return
        except OSError as e:
            with contextlib.suppress(OSError):
                os.unlink(self.request_file)
            raise SystemExit(f"request: {e}")
        with os.fdopen(fd, "rb") as f:
            raw = f.read(4096)
        with contextlib.suppress(OSError):
            os.unlink(self.request_file)
        try:
            req = json.loads(raw)
        except ValueError:
            return self.log("The app's request was not JSON; ignored")
        who = re.sub(r"[^\w.@-]", "", str(req.get("requested_by") or "app"))[:40]
        if req.get("action") == "check":
            return self.check(who)
        if req.get("action") == "install" and VERSION_RE.match(str(req.get("version") or "")):
            # Only what the last check verified as installable; the app can't name arbitrary versions.
            avail = self.status.get("available") or {}
            if avail.get("version") != req["version"] or not avail.get("installable"):
                self.check(who)
                avail = self.status.get("available") or {}
            if avail.get("version") == req["version"] and avail.get("installable"):
                return self.install(req["version"], who)
            return self.log(f"Version {req['version']} was not checked as installable; ignored")
        self.log("Unknown request from the app; ignored")


def main(argv):
    try:
        with open(CONF_FILE, encoding="utf-8") as f:
            conf = json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit(f"{CONF_FILE}: {e}")
    u = Updater(conf)
    os.makedirs(u.c["work_dir"], mode=0o700, exist_ok=True)
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(u.status, indent=1, ensure_ascii=False))
        return
    with open(os.path.join(u.c["work_dir"], "lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit("another update run is busy")
        if cmd == "run-request":
            u.run_request()
        elif cmd == "check":
            u.check("timer" if len(argv) < 3 else argv[2])
        elif cmd == "install" and len(argv) > 2:
            u.check("cli")
            if (u.status.get("available") or {}).get("version") != argv[2]:
                raise SystemExit(f"{argv[2]} is not the newest installable release; see: msd-updater.py status")
            u.install(argv[2], "cli")
        else:
            raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
