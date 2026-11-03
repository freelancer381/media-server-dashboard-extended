"""The update service against a fake GitHub, a stub cosign and a fake systemctl, all in a temp folder."""
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import threading
import unittest
import warnings

warnings.simplefilter("ignore", ResourceWarning)  # the helpers below open small files casually
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("msd_updater", os.path.join(ROOT, "updater", "msd-updater.py"))
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)

REPO = "acme/dash"


def make_archive(version, extra=None, version_file=None, bad_py=False):
    buf = io.BytesIO()
    prefix = f"media-server-dashboard-{version}/"
    files = {"VERSION": (version_file or version) + "\n", "server.py": "print('hi')\n" if not bad_py else "def (\n", "updater/msd-updater.py": "#\n"}
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(prefix + name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info, data in (extra or []):
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


class FakeGitHub:
    """Releases API + downloads + the app's /healthz (which reports whatever `current` points to)."""

    def __init__(self, app_root):
        self.releases = []
        self.files = {}
        self.app_root = app_root
        self.health_override = None
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                p = self.path
                if p == "/healthz":
                    try:
                        v = open(os.path.join(outer.app_root, "current", "VERSION")).read().strip()
                    except OSError:
                        v = None
                    return self.send(json.dumps({"version": outer.health_override or v}).encode())
                if p in (f"/repos/{REPO}", "/repos/old/dash"):  # GitHub forwards a moved repository
                    return self.send(json.dumps({"full_name": REPO}).encode())
                if p.startswith(f"/repos/{REPO}/releases/tags/"):
                    tag = p.rsplit("/", 1)[1]
                    r = next((r for r in outer.releases if r["tag_name"] == tag), None)
                    return self.send(json.dumps(r).encode()) if r else self.send(b"{}", 404)
                if p.startswith(f"/repos/{REPO}/releases"):
                    return self.send(json.dumps(outer.releases).encode())
                if p.startswith("/dl/"):
                    return self.send(outer.files[p])
                self.send(b"", 404)

            def send(self, body, code=200):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add(self, version, archive=None, policy=None, release_patch=None, prerelease=False, sha=None):
        archive = archive if archive is not None else make_archive(version)
        name = f"media-server-dashboard-{version}.tar.gz"
        rel = {"schema": 1, "version": version, "tag": f"v{version}",
               "artifact": {"name": name, "sha256": sha or hashlib.sha256(archive).hexdigest(), "size": len(archive)},
               "policy": policy or {"requiresUpdater": ">=1.0.0"}, "notes": f"Neu in {version}"}
        rel.update(release_patch or {})
        doc = json.dumps(rel).encode()
        assets = []
        for fname, data in ((name, archive), ("release.json", doc), ("release.json.sigstore.json", b'{"fake": "' + version.encode() + b'"}')):
            path = f"/dl/{version}/{fname}"
            self.files[path] = data
            assets.append({"name": fname, "browser_download_url": self.url + path})
        self.releases.append({"tag_name": f"v{version}", "name": f"Release {version}", "draft": False, "prerelease": prerelease,
                              "html_url": f"https://github.com/{REPO}/releases/v{version}", "assets": assets, "body": ""})


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)  # the helpers here open small files casually
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.app_root, self.data, self.work = f"{t}/opt", f"{t}/data", f"{t}/work"
        os.makedirs(f"{self.app_root}/releases/0.2.0")
        open(f"{self.app_root}/releases/0.2.0/VERSION", "w").write("0.2.0\n")
        os.symlink(f"{self.app_root}/releases/0.2.0", f"{self.app_root}/current")
        os.makedirs(f"{self.data}/updater")
        open(f"{self.data}/config.json", "w").write('{"title": "x"}')
        self.gh = FakeGitHub(self.app_root)
        # cosign stub: accepts only the identity of this repository's release workflow at the tag, and bundles not marked bad.
        self.cosign = f"{t}/cosign"
        with open(self.cosign, "w") as f:
            f.write(f"""#!/bin/sh
args="$*"
bundle=""; prev=""; for a in "$@"; do [ "$prev" = "--bundle" ] && bundle="$a"; prev="$a"; done
grep -q BAD "$bundle" && {{ echo "error: invalid signature" >&2; exit 1; }}
case "$args" in *"--certificate-identity https://github.com/{REPO}/.github/workflows/release.yml@refs/tags/v"*"--certificate-github-workflow-trigger push"*) exit 0;; esac
echo "error: wrong identity: $args" >&2; exit 1
""")
        os.chmod(self.cosign, 0o755)
        self.systemctl_log = f"{t}/systemctl.log"
        self.systemctl = f"{t}/systemctl"
        with open(self.systemctl, "w") as f:
            f.write(f"#!/bin/sh\necho \"$@\" >> {self.systemctl_log}\n")
        os.chmod(self.systemctl, 0o755)

    def tearDown(self):
        self.gh.server.shutdown()
        self.tmp.cleanup()

    def updater(self, **extra):
        return U.Updater({"repo": REPO, "api": self.gh.url, "app_root": self.app_root, "data_dir": self.data, "work_dir": self.work,
                          "cosign": self.cosign, "systemctl": self.systemctl, "health_url": self.gh.url + "/healthz",
                          "health_timeout": 3, "self_path": f"{self.tmp.name}/msd-updater.py", "verify_user": "", **extra})

    def current(self):
        return open(f"{self.app_root}/current/VERSION").read().strip()

    # ---------- check ----------
    def test_check_finds_newest_installable(self):
        self.gh.add("0.1.0")
        self.gh.add("0.3.0")
        self.gh.add("0.4.0", prerelease=True)
        u = self.updater()
        u.check()
        self.assertEqual(u.status["available"]["version"], "0.3.0")
        self.assertTrue(u.status["available"]["installable"])
        self.assertEqual(u.status["state"], "idle")
        self.assertEqual(json.load(open(u.status_file))["available"]["version"], "0.3.0")

    def test_check_lists_refused_and_falls_back(self):
        self.gh.add("0.3.0")
        self.gh.add("0.4.0", policy={"minimumFromVersion": "0.3.0"})
        u = self.updater()
        u.check()
        self.assertEqual(u.status["available"]["version"], "0.3.0")
        self.assertIn("0.3.0", u.status["refused"][0]["reason"])

    def test_bad_signature_refused(self):
        self.gh.add("0.3.0")
        self.gh.files["/dl/0.3.0/release.json.sigstore.json"] = b"BAD"
        u = self.updater()
        u.check()
        self.assertIsNone(u.status["available"])
        self.assertIn("invalid signature", u.status["refused"][0]["reason"])

    def test_wrong_repo_identity_refused(self):
        self.gh.add("0.3.0")
        u = self.updater()
        u.c["repo"] = "evil/dash"
        u.c["api"] = self.gh.url  # same releases, but the signature must be from evil/dash's workflow
        u.c["repo"] = "evil/dash"
        with self.assertRaises(U.Refused):
            u.verify("0.3.0", b"{}", b"{}", f"{self.work}/x")

    def test_policy_manual_steps_and_updater_version(self):
        u = self.updater()
        base = {"version": "0.3.0", "tag": "v0.3.0", "artifact": {"name": "a.tar.gz", "sha256": "0" * 64}}
        with self.assertRaisesRegex(U.Refused, "manual steps"):
            u.evaluate("0.3.0", json.dumps({**base, "policy": {"manualSteps": {"required": True, "summary": "Change the config"}}}), "0.2.0")
        with self.assertRaisesRegex(U.Refused, "newer update service"):
            u.evaluate("0.3.0", json.dumps({**base, "policy": {"requiresUpdater": ">=2.0.0"}}), "0.2.0")
        with self.assertRaisesRegex(U.Refused, "not newer"):
            u.evaluate("0.2.0", json.dumps({**base, "version": "0.2.0", "tag": "v0.2.0"}), "0.2.0")
        with self.assertRaisesRegex(U.Refused, "another version"):
            u.evaluate("0.3.0", json.dumps({**base, "version": "0.9.0"}), "0.2.0")

    # ---------- install ----------
    def test_install_success(self):
        self.gh.add("0.3.0")
        u = self.updater()
        u.check()
        u.install("0.3.0", "luis")
        self.assertEqual(u.status["last"]["result"], "succeeded", u.status["log"])
        self.assertEqual(u.status["state"], "idle")
        self.assertEqual(self.current(), "0.3.0")
        self.assertIn("restart media-server-dashboard", open(self.systemctl_log).read())
        backups = os.listdir(f"{self.work}/backups")
        self.assertEqual(len(backups), 1)
        with tarfile.open(f"{self.work}/backups/{backups[0]}") as tar:
            self.assertIn("config.json", tar.getnames())
        self.assertEqual(stat.S_IMODE(os.stat(f"{self.work}/backups/{backups[0]}").st_mode), 0o600)
        self.assertTrue(all(s["state"] == "done" for s in u.status["steps"]))

    def test_sha_mismatch_changes_nothing(self):
        self.gh.add("0.3.0", sha="1" * 64)
        u = self.updater()
        u.install("0.3.0")
        self.assertEqual(u.status["last"]["result"], "refused")
        self.assertIn("SHA-256", u.status["last"]["error"])
        self.assertEqual(self.current(), "0.2.0")
        self.assertFalse(os.path.exists(self.systemctl_log))

    def test_unhealthy_new_version_rolls_back(self):
        self.gh.add("0.3.0")
        u = self.updater()
        real = u.healthy
        calls = []

        def healthy(version):
            calls.append(version)
            if version == "0.3.0":
                raise U.Failed("health check: reports version 0.2.0")
            return real(version)
        u.healthy = healthy
        u.install("0.3.0")
        self.assertEqual(u.status["last"]["result"], "rolled_back", u.status["log"])
        self.assertEqual(self.current(), "0.2.0")
        self.assertEqual(calls, ["0.3.0", "0.2.0"])
        self.assertEqual(open(self.systemctl_log).read().count("restart"), 2)

    def test_path_traversal_in_archive(self):
        evil = tarfile.TarInfo("media-server-dashboard-0.3.0/../../etc/evil")
        evil.size = 1
        self.gh.add("0.3.0", archive=make_archive("0.3.0", extra=[(evil, b"x")]))
        u = self.updater()
        u.install("0.3.0")
        self.assertEqual(u.status["last"]["result"], "failed")
        self.assertIn("unsafe path", u.status["last"]["error"])
        self.assertEqual(self.current(), "0.2.0")

    def test_symlink_in_archive(self):
        link = tarfile.TarInfo("media-server-dashboard-0.3.0/x")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/shadow"
        self.gh.add("0.3.0", archive=make_archive("0.3.0", extra=[(link, None)]))
        u = self.updater()
        u.install("0.3.0")
        self.assertIn("links", u.status["last"]["error"])
        self.assertEqual(self.current(), "0.2.0")

    def test_broken_python_is_not_switched_to(self):
        self.gh.add("0.3.0", archive=make_archive("0.3.0", bad_py=True))
        u = self.updater()
        u.install("0.3.0")
        self.assertEqual(u.status["last"]["result"], "failed")
        self.assertEqual(self.current(), "0.2.0")

    def test_prune_keeps_a_few(self):
        u = self.updater(keep_releases=2)
        for v in ("0.3.0", "0.4.0", "0.5.0"):
            self.gh.add(v)
        for v in ("0.3.0", "0.4.0", "0.5.0"):
            u.install(v)
            self.assertEqual(self.current(), v)
        self.assertEqual(sorted(os.listdir(f"{self.app_root}/releases")), ["0.4.0", "0.5.0"])

    # ---------- requests from the app ----------
    def test_request_install_only_what_check_verified(self):
        self.gh.add("0.3.0")
        u = self.updater()
        json.dump({"action": "install", "version": "9.9.9", "requested_by": "app"}, open(u.request_file, "w"))
        u.run_request()
        self.assertEqual(self.current(), "0.2.0")
        self.assertFalse(os.path.exists(u.request_file))
        json.dump({"action": "install", "version": "0.3.0", "requested_by": "luis; rm -rf /"}, open(u.request_file, "w"))
        u.run_request()
        self.assertEqual(self.current(), "0.3.0")
        self.assertEqual(u.status["last"]["requested_by"], "luisrm-rf")

    def test_request_symlink_is_not_followed(self):
        u = self.updater()
        secret = f"{self.tmp.name}/secret.json"
        json.dump({"action": "check"}, open(secret, "w"))
        os.symlink(secret, u.request_file)
        with self.assertRaises(SystemExit):
            u.run_request()
        self.assertTrue(os.path.exists(secret))
        self.assertFalse(os.path.lexists(u.request_file))

    def test_status_file_not_written_through_symlink(self):
        u = self.updater()
        target = f"{self.tmp.name}/target"
        open(target, "w").write("keep")
        os.makedirs(os.path.dirname(u.status_file), exist_ok=True)
        os.symlink(target, u.status_file)
        u.save()
        self.assertEqual(open(target).read(), "keep")
        self.assertFalse(os.path.islink(u.status_file))

    def test_updates_itself_from_a_verified_release(self):
        mine = f"{self.tmp.name}/msd-updater.py"
        open(mine, "w").write('UPDATER_VERSION = "1.1.0"\n')
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, text in {"VERSION": "0.3.0\n", "server.py": "print(1)\n", "updater/msd-updater.py": 'UPDATER_VERSION = "9.0.0"\n# new\n'}.items():
                info = tarfile.TarInfo("media-server-dashboard-0.3.0/" + name)
                info.size = len(text.encode())
                tar.addfile(info, io.BytesIO(text.encode()))
        self.gh.add("0.3.0", archive=buf.getvalue())
        u = self.updater()
        u.install("0.3.0")
        self.assertEqual(u.status["last"]["result"], "succeeded", u.status["log"])
        self.assertIn('"9.0.0"', open(mine).read())

    def test_does_not_downgrade_itself(self):
        mine = f"{self.tmp.name}/msd-updater.py"
        open(mine, "w").write("keep")
        self.gh.add("0.3.0")  # its updater has no newer version
        u = self.updater()
        u.install("0.3.0")
        self.assertEqual(open(mine).read(), "keep")

    def test_follows_a_moved_repository(self):
        self.gh.add("0.3.0")
        u = self.updater(repo="old/dash")  # installed before the move; the signatures now come from acme/dash
        u.check()
        self.assertEqual(u.status["available"]["version"], "0.3.0", u.status)
        self.assertIn("moved", " ".join(u.status["log"]))

    def test_versions(self):
        self.assertTrue(U.satisfies("1.0.0", ">=1.0.0 <2.0.0"))
        self.assertFalse(U.satisfies("2.0.0", ">=1.0.0 <2.0.0"))
        with self.assertRaises(ValueError):
            U.vtuple("1.0")


if __name__ == "__main__":
    unittest.main()
