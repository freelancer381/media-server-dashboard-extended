"""The container's entrypoint: hands /data to PUID:PGID and drops root (run as root, as in CI's containers)."""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join(ROOT, "docker-entrypoint.py")
WHO = [sys.executable, "-c", "import os; print(os.getuid(), os.getgid(), os.getgroups(), os.environ['HOME'])"]


def run(env, data):
    """The entrypoint's own message first, then the started program's line (its user, groups and home)."""
    return subprocess.run([sys.executable, ENTRY, *WHO], capture_output=True, text=True, timeout=20,
                          env={"PATH": os.environ.get("PATH", ""), "MSD_DATA": data, **env})


@unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "needs root")
class Entrypoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.chmod(self.tmp, 0o755)
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(os.path.join(self.data, "sub"))
        for name in ("config.json", "sub/events.json"):
            open(os.path.join(self.data, name), "w").close()
        self.outside = os.path.join(self.tmp, "outside.txt")
        open(self.outside, "w").close()
        os.symlink(self.outside, os.path.join(self.data, "link"))  # must not hand over what it points to

    def owners(self):
        return {os.path.relpath(os.path.join(r, n), self.data): (os.lstat(os.path.join(r, n)).st_uid, os.lstat(os.path.join(r, n)).st_gid)
                for r, ds, fs in os.walk(self.data) for n in ds + fs}

    def test_unraid_ids(self):
        r = run({"PUID": "99", "PGID": "100"}, self.data)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[-1].split()[:3], ["99", "100", "[]"])  # no root, no extra groups
        self.assertEqual(r.stdout.splitlines()[-1].split()[3], self.data)
        self.assertEqual(set(self.owners().values()), {(99, 100)})
        self.assertEqual(os.stat(self.data).st_uid, 99)
        self.assertEqual(os.stat(self.outside).st_uid, 0)  # the link's target outside stays root's
        self.assertIn("handed", r.stdout.splitlines()[0])
        again = run({"PUID": "99", "PGID": "100"}, self.data)
        self.assertEqual(len(again.stdout.splitlines()), 1)  # nothing left to hand over: no message

    def test_default_is_1000(self):
        r = run({}, self.data)
        self.assertEqual(r.stdout.splitlines()[-1].split()[:2], ["1000", "1000"], r.stderr)

    def test_missing_folder_is_made(self):
        new = os.path.join(self.tmp, "fresh")
        r = run({"PUID": "99", "PGID": "100"}, new)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((os.stat(new).st_uid, os.stat(new).st_gid), (99, 100))

    def test_command_found_on_path(self):
        # The image's CMD is ["python3", "/app/server.py"]: a bare name, as Docker passes it
        r = subprocess.run([sys.executable, ENTRY, "python3", "-c", "import os; print(os.getuid())"], capture_output=True, text=True, timeout=20,
                           env={"PATH": os.path.dirname(sys.executable) + ":" + os.environ.get("PATH", ""), "MSD_DATA": self.data, "PUID": "99", "PGID": "100"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[-1], "99")

    def test_bad_value_is_refused(self):
        r = run({"PUID": "nobody"}, self.data)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PUID must be a number", r.stderr)


if __name__ == "__main__":
    unittest.main()
