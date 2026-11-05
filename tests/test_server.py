import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_tmp = tempfile.mkdtemp()
os.environ["MSD_DATA"] = _tmp
os.environ["MSD_UPDATER_CONF"] = os.path.join(_tmp, "none.json")
sys.path.insert(0, ROOT)
import server  # noqa: E402


def wizard(**over):
    base = {"title": "T", "user": "luis", "password": "long-enough-pw", "apps": {}, "custom_apps": [], "home": {}}
    base.update(over)
    return base


class BuildConfig(unittest.TestCase):
    def test_minimal(self):
        c = server.build_config(wizard())
        self.assertEqual(c["auth"]["user"], "luis")
        self.assertNotIn("long-enough-pw", json.dumps(c))
        self.assertTrue(c["webhook_token"])
        self.assertTrue(c["game"]["enabled"])

    def test_validation(self):
        for bad, msg in [
            (wizard(user="a b"), "User name"),
            (wizard(password="short"), "10 characters"),
            (wizard(domain="not a domain"), "domain"),
            (wizard(apps={"radarr": {"enabled": True, "url": "ftp://x", "api_key": "k"}}), "URL"),
            (wizard(apps={"radarr": {"enabled": True, "url": "http://x:7878"}}), "API key"),
            (wizard(apps={"radarr": {"enabled": True, "url": "http://x:7878", "api_key": "k"}}, custom_apps=[{"name": "Radarr", "url": "http://x"}]), "used twice"),
            (wizard(home={"enabled": True, "tiles": [{"name": "X"}]}), "URL or IP:port"),
            (wizard(home={"enabled": True, "tiles": [{"name": "X", "url": "javascript:alert(1)"}]}), "URL"),
            (wizard(home={"enabled": True, "tiles": [{"name": "X", "check": "a b:1"}]}), "IP:port"),
            (wizard(home={"enabled": True, "tiles": [{"name": "X", "check": "h:1", "icon": "data:text/html;base64,AAAA"}]}), "icon"),
        ]:
            with self.subTest(msg=msg):
                with self.assertRaisesRegex(ValueError, msg):
                    server.build_config(bad)

    def test_new_password_needs_the_current_one(self):
        server.CONFIG.clear()
        server.CONFIG.update({"auth": {"user": "luis", **server.hash_password("old-password-1", iterations=1000)}})
        try:
            with self.assertRaisesRegex(ValueError, "current one"):
                server.build_config(wizard(password="new-password-1"))
            with self.assertRaisesRegex(ValueError, "current one"):
                server.build_config(wizard(password="new-password-1", current_password="wrong"))
            c = server.build_config(wizard(password="new-password-1", current_password="old-password-1"))
            self.assertEqual(server.hash_password("new-password-1", c["auth"]["salt"], c["auth"]["iterations"])["hash"], c["auth"]["hash"])
        finally:
            server.CONFIG.clear()

    def test_saved_secrets_only_go_to_their_own_server(self):
        server.CONFIG.clear()
        server.CONFIG.update({"auth": {"user": "luis", "salt": "00", "hash": "ab", "iterations": 1},
                              "apps": {"radarr": {"url": "http://10.0.0.21:7878", "api_key": "secret-key"}},
                              "proxmox": {"url": "https://10.0.0.1:8006", "node": "pve", "token_id": "t@pve!x", "token_secret": "pve-secret"}})
        try:
            with self.assertRaisesRegex(ValueError, "needs the key again"):
                server.build_config(wizard(password="", apps={"radarr": {"enabled": True, "url": "http://evil.example:7878"}}))
            with self.assertRaisesRegex(ValueError, "needs the secret again"):
                server.build_config(wizard(password="", proxmox={"url": "https://evil.example:8006", "node": "pve", "token_id": "t@pve!x"}))
            c = server.build_config(wizard(password="", apps={"radarr": {"enabled": True, "url": "http://10.0.0.21:7878/"}}))
            self.assertEqual(c["apps"]["radarr"]["api_key"], "secret-key")  # same server: kept
            self.assertEqual(server.keep_secret("http://a:1", "https://a:1", "k"), "")  # another scheme is another server
        finally:
            server.CONFIG.clear()

    def test_secrets_are_kept_when_left_empty(self):
        server.CONFIG.clear()
        server.CONFIG.update({"auth": {"user": "luis", "salt": "00", "hash": "ab", "iterations": 1}, "webhook_token": "keep-me",
                              "apps": {"radarr": {"url": "http://r:7878", "api_key": "old-key"}}})
        try:
            c = server.build_config(wizard(password="", apps={"radarr": {"enabled": True, "url": "http://r:7878", "api_key": ""}}))
            self.assertEqual(c["auth"]["hash"], "ab")
            self.assertEqual(c["apps"]["radarr"]["api_key"], "old-key")
            self.assertEqual(c["webhook_token"], "keep-me")
            self.assertNotEqual(server.build_config(wizard(password="", new_webhook_token=True))["webhook_token"], "keep-me")
        finally:
            server.CONFIG.clear()


class Helpers(unittest.TestCase):
    def test_tidy(self):
        self.assertEqual(server.tidy("Atlantis.The.Lost.Empire.2001.1080p.BluRay"), "Atlantis The Lost Empire (2001)")
        self.assertEqual(server.tidy("Some.Show.S02E05.720p"), "Some Show S02E05")

    def test_tile_target(self):
        self.assertEqual(server.tile_target({"url": "https://jf.home.lan"}), ("jf.home.lan", 443))
        self.assertEqual(server.tile_target({"url": "http://x", "check": "10.0.0.5:5000"}), ("10.0.0.5", 5000))
        self.assertEqual(server.tile_target({"check": "nas.lan"}), ("nas.lan", 80))

    def test_trusted_proxies_take_ranges(self):
        n = server.Networks(["172.16.0.0/12", "10.0.0.5", "fd00::/8", "not an address"])
        self.assertEqual([ip in n for ip in ("172.18.0.1", "172.32.0.1", "10.0.0.5", "10.0.0.6", "::ffff:172.20.0.1", "fd00::1", "", "x")],
                         [True, False, True, False, True, True, False, False])
        self.assertFalse(server.Networks([]))

    def test_caddy_site(self):
        self.assertIn("http:// {", server.caddy_site({}))
        site = server.caddy_site({"domain": "d.home.lan", "tls": "internal", "ip_access": False})
        self.assertIn("tls internal", site)
        self.assertNotIn("http://", site)

    def test_caddy_site_behind_proxy(self):
        site = server.caddy_site({"behind_proxy": True, "domain": "dash.example.org"})
        self.assertNotIn("dash.example.org", site)  # the user's proxy does the domain and HTTPS: no redirect loop
        self.assertIn("http:// {", site)
        self.assertIn("header_up X-Msd-Forwarded-For", site)

    def test_password_hash(self):
        h = server.hash_password("pw", iterations=1000)
        self.assertEqual(server.hash_password("pw", h["salt"], 1000)["hash"], h["hash"])
        self.assertNotEqual(server.hash_password("pw2", h["salt"], 1000)["hash"], h["hash"])

    def test_app_urls(self):
        sab = server.make_app("sabnzbd", {"url": "http://s:8080/", "api_key": "k y"}, server.BUILTIN["sabnzbd"])
        self.assertEqual(server.app_url(sab, "queue")[0], "http://s:8080/api?output=json&apikey=k%20y&mode=queue")
        plex = server.make_app("custom", {"url": "http://p:32400", "api_key": "t", "auth": "query", "auth_name": "X-Plex-Token", "kind": "generic"})
        self.assertEqual(server.app_url(plex, "/identity")[0], "http://p:32400/identity?X-Plex-Token=t")
        radarr = server.make_app("radarr", {"url": "http://r:7878/radarr", "api_key": "k"}, server.BUILTIN["radarr"])
        self.assertEqual(server.app_url(radarr, "health"), ("http://r:7878/radarr/api/v3/health", {"X-Api-Key": "k"}))


if __name__ == "__main__":
    unittest.main()


def downloaders(*apps):
    """Make these the dashboard's download clients for one test; returns what to restore."""
    saved = (dict(server.BY_KIND), list(server.DOWNLOADERS), set(server.DL_NAMES), dict(server.dl_state), dict(server.dl_seen), dict(server.qb_cookies))
    for a in apps:
        server.BY_KIND[a["key"]] = a
    server.DOWNLOADERS[:] = list(apps)
    server.DL_NAMES.clear()
    server.DL_NAMES.update(a["name"] for a in apps)
    for d in (server.dl_state, server.dl_seen, server.qb_cookies, server.qb_alltime, server.qb_session):
        d.clear()
    saved = saved + (server.STATS,)
    server.STATS = server.historymod.Recorder(os.path.join(tempfile.mkdtemp(), "stats.json"))  # a fresh count per test
    return saved


def restore(saved):
    server.STATS = saved[-1]
    for target, value in zip((server.BY_KIND, server.DOWNLOADERS, server.DL_NAMES, server.dl_state, server.dl_seen, server.qb_cookies), saved):
        target.clear()
        (target.update if not isinstance(target, list) else target.extend)(value)


class NZBGet(unittest.TestCase):
    """read_nzbget against a fake JSON-RPC server."""

    def setUp(self):
        import base64
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.queue = [{"NZBID": 7, "NZBName": "Dune.2021.2160p", "Status": "DOWNLOADING", "FileSizeLo": 4 * 2**30, "FileSizeHi": 0,
                       "RemainingSizeLo": 2**30, "RemainingSizeHi": 0, "Parameters": [{"Name": "drone", "Value": "abc123"}]}]
        self.history = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                if self.headers.get("Authorization") != "Basic " + base64.b64encode(b"nzbget:secret").decode():
                    self.send_response(401)
                    self.end_headers()
                    return
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                res = {"status": {"DownloadRate": 5 * 2**20, "RemainingSizeLo": 2**30, "RemainingSizeHi": 0, "DownloadPaused": False},
                       "listgroups": outer.queue, "history": outer.history, "version": "24.3",
                       "servervolumes": [{"ServerID": 0, "DaySlot": 1, "TotalSizeLo": 0, "TotalSizeHi": 2,
                                          "BytesPerDays": [{"SizeLo": 100, "SizeHi": 0}, {"SizeLo": 2**30, "SizeHi": 0}]}]}[req["method"]]
                body = json.dumps({"id": req["id"], "result": res}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.app = server.make_app("nzbget", {"url": f"http://127.0.0.1:{self.srv.server_port}", "username": "nzbget", "api_key": "secret"}, server.BUILTIN["nzbget"])
        self.saved = downloaders(self.app)

    def tearDown(self):
        self.srv.shutdown()
        restore(self.saved)

    def test_queue_speed_and_totals(self):
        server.read_nzbget()
        sab = server.state["sab"]
        self.assertTrue(sab["ok"], sab)
        self.assertEqual((sab["client"], sab["version"], sab["queueTotal"]), ("NZBGet", "24.3", 1))
        q = sab["queue"][0]
        self.assertEqual(q["id"], "abc123")  # the id Radarr/Sonarr know it by
        self.assertAlmostEqual(q["pct"], 75.0)
        self.assertEqual(sab["speed"], 5 * 2**20)
        self.assertEqual(sab["totals"]["day"], 2**30)
        self.assertEqual(sab["totals"]["week"], 2**30 + 100)
        self.assertEqual(sab["totals"]["total"], 2 << 32)

    def test_finished_download_reaches_the_feed(self):
        server.read_nzbget()
        self.queue.clear()
        self.history.append({"NZBID": 7, "Name": "Dune.2021.2160p", "Status": "SUCCESS/ALL", "Parameters": [{"Name": "drone", "Value": "abc123"}]})
        server.read_nzbget()
        last = server.LIVE.recent(3)
        self.assertTrue(any(e["source"] == "NZBGet" and e["kind"] == "download" and "Dune (2021)" in e["text"] for e in last), last)

    def test_wrong_password(self):
        self.app["api_key"] = "nope"
        server.read_nzbget()
        self.assertFalse(server.state["sab"]["ok"])
        self.assertEqual(server.state["sab"]["client"], "NZBGet")


class Posters(unittest.TestCase):
    def test_open_link_and_fallbacks(self):
        link = server.open_link("movie", "Dune", 2021, "dune-2021")
        self.assertTrue(link.startswith("/api/open?") and "title=Dune" in link and "slug=dune-2021" in link)
        saved = dict(server.BY_KIND)
        try:
            server.BY_KIND["radarr"] = server.make_app("radarr", {"url": "http://r:7878", "api_key": "k", "web_url": "https://radarr.home.lan"}, server.BUILTIN["radarr"])
            self.assertEqual(server.open_target("movie", "Dune", "2021", "dune-2021"), "https://radarr.home.lan/movie/dune-2021")
            self.assertIsNone(server.open_target("movie", "Dune", "2021", "../../evil"))
        finally:
            server.BY_KIND.clear()
            server.BY_KIND.update(saved)

    def test_old_updater_status_shows_in_english(self):
        st = server.english({"steps": [{"name": "Signatur prüfen"}], "log": ["21:00 Fertig"], "last": {"error": "Signatur ungültig: x"}})
        self.assertEqual(st["steps"][0]["name"], "Check signature")
        self.assertEqual(st["log"], ["21:00 Done"])
        self.assertEqual(st["last"]["error"], "invalid signature: x")


class LocalHost(unittest.TestCase):
    """Without Proxmox (Docker, Synology) the host's numbers come from /proc."""

    def write(self, d, cpu, rx, sectors):
        os.makedirs(f"{d}/net", exist_ok=True)
        files = {
            "stat": f"cpu  {cpu[0]} 0 {cpu[1]} {cpu[2]} {cpu[3]} 0 0 0 0 0\ncpu0 1 0 1 1 0 0 0 0 0 0\n",
            "meminfo": "MemTotal:       16384000 kB\nMemFree:         1000000 kB\nMemAvailable:    4096000 kB\nSwapTotal:       2048000 kB\nSwapFree:        1024000 kB\n",
            "loadavg": "1.50 1.25 1.00 2/300 1234\n", "uptime": "86400.00 1000.00\n",
            "net/dev": f"Inter-|   Receive\n face |bytes\n    lo: 999 0 0 0 0 0 0 0 999 0 0 0 0 0 0 0\n  eth0: {rx} 10 0 0 0 0 0 0 500 5 0 0 0 0 0 0\n",
            "diskstats": f"   8       0 sda 10 0 {sectors} 0 10 0 {sectors} 0 0 0 0\n   8       1 sda1 10 0 999999 0 10 0 999999 0 0 0 0\n   7       0 loop0 1 0 999999 0 1 0 0 0 0 0 0\n",
            "cpuinfo": "processor\t: 0\nmodel name\t: Intel(R) Celeron(R) J4125 CPU @ 2.00GHz\n",
            "mounts": f"/dev/sda1 {d} ext4 rw 0 0\n/dev/sda1 /etc/hostname ext4 rw 0 0\nproc /proc proc rw 0 0\n",
        }
        for name, text in files.items():
            with open(f"{d}/{name}", "w") as f:
                f.write(text)

    def test_cpu_memory_network_disk(self):
        d = tempfile.mkdtemp()
        saved = ({k: list(v) for k, v in server.history.items()}, dict(server.state["system"]), dict(server.local_cpu))
        try:
            for c in server.counters.values():
                c.total = c.at = None
            server.local_cpu.clear()
            self.write(d, (100, 100, 800, 0), 1000, 0)
            server.read_local(d)
            server.counters["rx"].at -= 2; server.counters["disk"].at -= 2  # two seconds later
            self.write(d, (150, 150, 900, 0), 1000 + 2 * 1048576, 4096)
            server.read_local(d)
            s = server.state["system"]
            self.assertEqual((s["source"], s["cpu_name"], s["load"]), ("local", "Intel(R) Celeron(R) J4125 CPU @ 2.00GHz", [1.5, 1.25, 1.0]))
            self.assertEqual(s["cpu"]["idle"], 50.0)  # 100 of 200 ticks busy
            self.assertEqual((s["memory"]["total"], s["memory"]["used"]), (16384000 * 1024, (16384000 - 4096000) * 1024))
            self.assertEqual(s["memory"]["swap_used"], 1024000 * 1024)
            self.assertEqual(server.history["cpu"][-1], 50)
            self.assertAlmostEqual(server.history["rx"][-1], 1048576, delta=2000)  # lo left out
            self.assertAlmostEqual(server.history["disk"][-1], 4096 * 512 * 2 / 2, delta=2000)  # whole disks only, not partitions or loops
            server.read_local_disks(d)
            self.assertEqual([x["name"] for x in server.state["disks"]], [d])  # the bind-mounted file and /proc are left out
            self.assertNotIn("Not set up yet", " ".join(a["message"] for a in server.alerts()))
        finally:
            for k, v in saved[0].items():
                server.history[k].clear(); server.history[k].extend(v)
            server.state["system"] = saved[1]
            server.local_cpu.clear(); server.local_cpu.update(saved[2])


class HiddenProblems(unittest.TestCase):
    """A dismissed problem stays hidden for the chosen time, also when it goes away and comes back in between."""

    def setUp(self):
        self.saved = (dict(server.snoozed), server.SNOOZE_FILE, server.alerts)
        server.snoozed.clear()
        server.SNOOZE_FILE = os.path.join(tempfile.mkdtemp(), "snoozed.json")
        self.now = [{"level": "warning", "source": "Memory", "message": "Memory is running low"},
                    {"level": "warning", "source": "SABnzbd", "message": "3 failed download(s) in history"}]
        server.alerts = lambda: list(self.now)

    def tearDown(self):
        server.snoozed.clear(); server.snoozed.update(self.saved[0])
        server.SNOOZE_FILE, server.alerts = self.saved[1], self.saved[2]

    def test_hidden_for_a_while_and_for_good(self):
        mem, sab = (server.alert_key(a) for a in self.now)
        self.assertEqual(sab, "SABnzbd|# failed download(s) in history")  # numbers don't make it a new problem
        server.snooze(mem, "1h")
        server.snooze(sab, "forever")
        shown, hidden = server.visible_alerts()
        self.assertEqual(shown, [])
        self.assertEqual({h["source"]: h["until"] is None for h in hidden}, {"Memory": False, "SABnzbd": True})
        self.now = [{"level": "warning", "source": "SABnzbd", "message": "4 failed download(s) in history"}]  # memory fine again, one more failure
        self.assertEqual(server.visible_alerts()[0], [])
        self.now.append({"level": "warning", "source": "Memory", "message": "Memory is running low"})  # back within the hour: still hidden
        self.assertEqual(server.visible_alerts()[0], [])
        server.snoozed[mem] = time.time() - 1  # the hour is over
        self.assertEqual([a["source"] for a in server.visible_alerts()[0]], ["Memory"])
        server.snooze(sab, "show")
        self.assertEqual(len(server.visible_alerts()[0]), 2)

    def test_kept_on_disk_and_checked(self):
        server.snooze("Memory|Memory is running low", "1w")
        self.assertIn("Memory|Memory is running low", json.load(open(server.SNOOZE_FILE)))
        for bad in [("no separator", "1h"), ("Memory|x", "a year")]:
            with self.assertRaises(ValueError):
                server.snooze(*bad)

    def test_hidden_problems_make_no_chat_lines(self):
        server.snooze("Memory|Memory is running low", "forever")
        before = server.alert_seen
        try:
            server.alert_seen = {}
            n = len(server.LIVE.recent(500))
            server.watch_alerts()
            texts = [e["text"] for e in server.LIVE.recent(500)[n:]]
            self.assertNotIn("Memory is running low", texts)
            self.assertTrue(any("failed download" in t for t in texts))
        finally:
            server.alert_seen = before


class CaddyHelper(unittest.TestCase):
    """scripts/msd-caddy.py (root) writes Caddy's site; it must match the app's view and let nothing odd through."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("msd_caddy", os.path.join(ROOT, "scripts", "msd-caddy.py"))
        self.h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.h)

    def test_same_site_as_the_app(self):
        for c in ({}, {"domain": "dash.example.org"}, {"domain": "dash.home.lan", "tls": "internal", "ip_access": False},
                  {"behind_proxy": True, "domain": "dash.example.org"}, {"port": 9000}):
            with self.subTest(c=c):
                self.assertEqual(self.h.render(self.h.checked(c)), server.caddy_site({"port": 8900, **c}))

    def test_odd_values_dont_reach_caddy(self):
        c = self.h.checked({"domain": "evil.com {\n respond \"x\"\n}\nfoo", "port": "80; import /etc/passwd", "behind_proxy": "yes"})
        self.assertEqual(c["domain"], "")
        self.assertEqual(c["port"], 8900)
        self.assertFalse(c["behind_proxy"])
        self.assertNotIn("evil", self.h.render(c))

    def test_writes_from_the_settings_file(self):
        d = tempfile.mkdtemp()
        json.dump({"behind_proxy": True, "auth": {"hash": "secret"}}, open(f"{d}/config.json", "w"))
        os.environ["MSD_CADDY_NO_RELOAD"] = "1"
        try:
            self.h.main(["x", f"{d}/config.json", f"{d}/site.caddy"])
        finally:
            os.environ.pop("MSD_CADDY_NO_RELOAD")
        site = open(f"{d}/site.caddy").read()
        self.assertIn("X-Msd-Forwarded-For", site)
        self.assertNotIn("secret", site)
        os.symlink(f"{d}/config.json", f"{d}/link.json")  # the app's file is never followed through a link
        self.assertEqual(self.h.read_settings(f"{d}/link.json"), {})


class FakeQB:
    """A qBittorrent Web API (rdtclient answers the same) with user admin / password secret."""

    def __init__(self, transfer=True):
        import threading
        import urllib.parse
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.torrents = [
            {"hash": "aaa111", "name": "Dune.2021.2160p", "progress": 0.25, "size": 8 * 2**30, "amount_left": 6 * 2**30, "eta": 600,
             "state": "downloading", "dlspeed": 3 * 2**20, "priority": 1, "added_on": 10},
            {"hash": "bbb222", "name": "Show.S01E02.1080p", "progress": 0.0, "size": 2**30, "amount_left": 2**30, "eta": 8640000,
             "state": "queuedDL", "dlspeed": 0, "priority": 2, "added_on": 20},
            {"hash": "ccc333", "name": "Old.Seed", "progress": 1.0, "size": 2**30, "amount_left": 0, "eta": 8640000,
             "state": "stalledUP", "dlspeed": 0, "priority": 0, "added_on": 1}]
        self.logins = 0
        self.session_bytes = 0
        self.referers = set()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body, cookie=None):
                body = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
                outer.referers.add(self.headers.get("Referer"))
                if self.path == "/api/v2/auth/login" and form == {"username": ["admin"], "password": ["secret"]}:
                    outer.logins += 1
                    return self.send(200, b"Ok.", f"SID=s{outer.logins}; HttpOnly; path=/")
                self.send(200, b"Fails.")

            def do_GET(self):
                if self.headers.get("Cookie") != f"SID=s{outer.logins}" or not outer.logins:
                    return self.send(403, b"Forbidden")
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                if u.path == "/api/v2/app/version":
                    return self.send(200, b"v5.0.2")
                if u.path == "/api/v2/transfer/info" and transfer:
                    outer.session_bytes += 300 * 2**20  # what it downloaded since the last look
                    return self.send(200, {"dl_info_speed": 4 * 2**20, "dl_info_data": outer.session_bytes})
                if u.path == "/api/v2/sync/maindata" and transfer:
                    return self.send(200, {"server_state": {"alltime_dl": 7 * 2**40}})
                if u.path == "/api/v2/torrents/info":
                    ts = outer.torrents
                    if q.get("filter") == ["downloading"]:
                        ts = [t for t in ts if t["progress"] < 1 and t["state"] not in ("error",)]
                    if q.get("filter") == ["errored"]:
                        ts = [t for t in ts if t["state"] == "error"]
                    if "hashes" in q:
                        ts = [t for t in ts if t["hash"] in q["hashes"][0].split("|")]
                    return self.send(200, ts)
                self.send(404, b"Not Found")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"


class QBittorrent(unittest.TestCase):
    """read_qbittorrent against a fake qBittorrent Web API."""

    def setUp(self):
        self.qb = FakeQB()
        self.app = server.make_app("qbittorrent", {"url": self.qb.url, "username": "admin", "api_key": "secret"}, server.BUILTIN["qbittorrent"])
        self.saved = downloaders(self.app)

    def tearDown(self):
        self.qb.srv.shutdown()
        restore(self.saved)

    def test_queue_speed_and_totals(self):
        server.read_qbittorrent("qbittorrent")
        sab = server.state["sab"]
        self.assertTrue(sab["ok"], sab)
        self.assertEqual((sab["client"], sab["version"], sab["queueTotal"]), ("qBittorrent", "v5.0.2", 2))  # the seeding one isn't queued
        self.assertEqual([q["id"] for q in sab["queue"]], ["aaa111", "bbb222"])
        self.assertEqual((sab["queue"][0]["pct"], sab["queue"][0]["left"], sab["queue"][1]["left"], sab["queue"][1]["state"]), (25.0, "0:10:00", "", "Queued"))
        self.assertEqual((sab["speed"], sab["left"]), (4 * 2**20, 7 * 2**30))
        self.assertEqual(sab["totals"], {"day": 0, "week": 0, "month": 0, "total": 7 * 2**40})  # nothing counted yet
        server.read_qbittorrent("qbittorrent")
        server.read_qbittorrent("qbittorrent")
        t = server.state["sab"]["totals"]
        self.assertEqual((t["day"], t["week"], t["month"]), (600 * 2**20,) * 3)  # the dashboard counts qBittorrent's days itself
        self.qb.session_bytes = 0  # qBittorrent restarted: its session count starts over
        server.read_qbittorrent("qbittorrent")
        self.assertEqual(server.state["sab"]["totals"]["day"], 900 * 2**20)
        self.assertEqual(self.qb.referers, {self.qb.url})  # qBittorrent's CSRF check wants its own address

    def test_finished_and_failed_reach_the_feed(self):
        server.read_qbittorrent("qbittorrent")
        self.qb.torrents[0].update(progress=1.0, state="uploading", amount_left=0)
        self.qb.torrents[1].update(state="error")
        server.read_qbittorrent("qbittorrent")
        last = server.LIVE.recent(4)
        self.assertTrue(any(e["source"] == "qBittorrent" and e["kind"] == "download" and "Dune (2021)" in e["text"] for e in last), last)
        self.assertTrue(any(e["kind"] == "error" and "Show S01E02" in e["text"] for e in last), last)
        self.assertEqual(server.state["sab"]["failed"], 1)

    def test_signs_in_again_when_the_session_ran_out(self):
        server.read_qbittorrent("qbittorrent")
        server.qb_cookies["qbittorrent"] = "SID=expired"
        server.read_qbittorrent("qbittorrent")
        self.assertTrue(server.state["sab"]["ok"])
        self.assertEqual(self.qb.logins, 2)

    def test_wrong_password(self):
        self.app["api_key"] = "nope"
        server.read_qbittorrent("qbittorrent")
        self.assertFalse(server.state["sab"]["ok"])
        self.assertEqual(server.state["sab"]["client"], "qBittorrent")
        saved, server.state["services"] = server.state["services"], [{"name": "qBittorrent", "key": "qbittorrent", "up": True}]
        try:
            self.assertIn("Reachable, but the API isn't answering: check the URL and API key",
                          [a["message"] for a in server.alerts() if a["source"] == "qBittorrent"])
        finally:
            server.state["services"] = saved

    def test_rdtclient_without_transfer_info(self):
        rd = FakeQB(transfer=False)
        try:
            app = server.make_app("rdtclient", {"url": rd.url, "username": "admin", "api_key": "secret"}, server.BUILTIN["rdtclient"])
            self.assertEqual(app["kind"], "qbittorrent")
            downloaders(self.app, app)
            server.read_qbittorrent("qbittorrent")
            server.read_qbittorrent("rdtclient")
            sab = server.state["sab"]
            self.assertTrue(sab["ok"], sab)
            self.assertEqual(server.dl_state["rdtclient"]["speed"], 3 * 2**20)  # from the torrents' own speeds
            self.assertEqual(sab["client"], "qBittorrent + rdtclient")
            self.assertEqual((sab["queueTotal"], sab["speed"]), (4, 7 * 2**20))
            self.assertEqual([q["client"] for q in sab["queue"]], ["qBittorrent", "rdtclient", "qBittorrent", "rdtclient"])  # taking turns
            self.assertEqual(sab["totals"]["total"], 7 * 2**40)  # rdtclient has no all-time number; qBittorrent's still shows
            server.qb_session["rdtclient"]["at"] -= 10  # ten seconds later, at the torrents' 3 MiB/s
            server.read_qbittorrent("rdtclient")
            self.assertAlmostEqual(server.dl_state["rdtclient"]["totals"]["day"], 30 * 2**20, delta=2**20)
            self.assertEqual([x["title"] for x in server.up_next("rdtclient")], ["Dune (2021)", "Show S01E02"])
            self.assertTrue(server.activity("qBittorrent").startswith("Downloading Dune (2021)"))
        finally:
            rd.srv.shutdown()

    def test_one_of_two_clients_down(self):
        other = server.make_app("rdtclient", {"url": "http://127.0.0.1:9", "username": "admin", "api_key": "x"}, server.BUILTIN["rdtclient"])
        downloaders(self.app, other)
        server.read_qbittorrent("qbittorrent")
        server.read_qbittorrent("rdtclient")
        self.assertTrue(server.state["sab"]["ok"])
        self.assertFalse(server.app_state("rdtclient")["ok"])  # its own box says so

    def test_wizard_test_and_config(self):
        r = server.wizard_test({"what": "app", "app": {"key": "qbittorrent", "url": self.qb.url, "username": "admin", "api_key": "secret"}})
        self.assertEqual(r, {"ok": True, "version": "v5.0.2"})
        self.assertNotIn("qbittorrent", server.qb_cookies)  # the test signs in on its own
        bad = server.wizard_test({"what": "app", "app": {"key": "qbittorrent", "url": self.qb.url, "username": "admin", "api_key": "nope"}})
        self.assertFalse(bad["ok"])
        # No password needed (qBittorrent's bypass for the dashboard's address).
        c = server.build_config(wizard(apps={"qbittorrent": {"enabled": True, "url": self.qb.url}}))
        self.assertEqual(c["apps"]["qbittorrent"]["api_key"], "")
        # A custom "qBittorrent" from before it was built in still saves, as long as the built-in one is off...
        server.build_config(wizard(custom_apps=[{"name": "qBittorrent", "url": "http://10.0.0.5:8080", "kind": "generic", "auth": "none"}]))
        # ...but not both under one name.
        with self.assertRaisesRegex(ValueError, "used twice"):
            server.build_config(wizard(apps={"qbittorrent": {"enabled": True, "url": self.qb.url}},
                                       custom_apps=[{"name": "qBittorrent", "url": "http://10.0.0.5:8080", "kind": "generic", "auth": "none"}]))
        self.assertEqual(server.configured_apps({"apps": {"rdtclient": {"url": "http://10.0.0.6:6500"}}})[0]["kind"], "qbittorrent")


class Jellyfin(unittest.TestCase):
    """read_jellyfin against a fake Jellyfin API."""

    def setUp(self):
        import threading
        import urllib.parse
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.sessions = [{"Id": "s1", "UserName": "Alex", "DeviceName": "Living room TV", "Client": "Jellyfin Android TV",
                          "NowPlayingItem": {"Id": "i1", "Type": "Episode", "Name": "Hello, Ms. Cobel", "SeriesName": "Severance", "ParentIndexNumber": 2, "IndexNumber": 1, "RunTimeTicks": 1000},
                          "PlayState": {"PositionTicks": 250, "IsPaused": False, "PlayMethod": "DirectPlay"}},
                         {"Id": "s2", "UserName": "Sam", "NowPlayingItem": None}]  # signed in, watching nothing
        self.tokens = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.tokens.append(self.headers.get("X-Emby-Token"))
                if self.headers.get("X-Emby-Token") != "key":
                    self.send_response(401); self.end_headers(); return
                u = urllib.parse.urlparse(self.path)
                body = {"/System/Info": {"Version": "10.10.3", "ServerName": "media", "Id": "abc"},
                        "/Sessions": outer.sessions,
                        "/Users": [{"Id": "u1", "Name": "Alex", "Policy": {}}, {"Id": "u2", "Name": "Sam", "Policy": {}},
                                   {"Id": "u3", "Name": "Old", "Policy": {"IsDisabled": True}}],
                        "/Items/Counts": {"MovieCount": 412, "SeriesCount": 64, "EpisodeCount": 2210},
                        "/Users/u1/Items": {"Items": [{"Type": "Movie", "Name": "Dune", "ProductionYear": 2021, "UserData": {"LastPlayedDate": "2026-11-04T20:15:00.0000000Z", "Played": True}}]},
                        "/Users/u2/Items": {"Items": [{"Type": "Episode", "Name": "x", "SeriesName": "Andor", "ParentIndexNumber": 1, "IndexNumber": 3,
                                                       "UserData": {"LastPlayedDate": "2026-11-05T09:00:00Z", "PlayedPercentage": 64}}]}}.get(u.path)
                data = json.dumps(body).encode()
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.app = server.make_app("jellyfin", {"url": f"http://127.0.0.1:{self.srv.server_port}", "api_key": "key"}, server.BUILTIN["jellyfin"])
        self.saved = (dict(server.BY_KIND), dict(server.state["cinema"]), dict(server.state.get("jellyfin", {})))
        server.BY_KIND["jellyfin"] = self.app
        server.jf_slow.clear(); server.jf_slow["at"] = 0
        server.jf_seen.clear()

    def tearDown(self):
        self.srv.shutdown()
        server.BY_KIND.clear(); server.BY_KIND.update(self.saved[0])
        server.state["cinema"], server.state["jellyfin"] = self.saved[1], self.saved[2]

    def test_streams_profiles_watched_library(self):
        server.read_jellyfin()
        cin, jf = server.state["cinema"], server.state["jellyfin"]
        self.assertTrue(cin["enabled"] and cin["ok"] and jf["ok"])
        self.assertEqual(cin["profiles"], ["Alex", "Sam"])  # the disabled user is left out
        self.assertEqual(cin["streams"], [{"title": "Severance S02E01", "who": "Alex", "pct": 25, "paused": False, "transcoding": False,
                                           "device": "Living room TV", "client": "Jellyfin Android TV", "service": "Jellyfin"}])
        self.assertEqual([(w["who"], w["title"], w["pct"]) for w in cin["watched"]], [("Sam", "Andor S01E03", 64), ("Alex", "Dune (2021)", 100)])  # newest first
        self.assertEqual((jf["version"], jf["library"]["movies"], jf["library"]["episodes"]), ("10.10.3", 412, 2210))
        self.assertEqual(server.activity("Jellyfin"), "Streaming Severance S02E01 for Alex")
        self.assertEqual(server.media_server()[0], "jellyfin")  # posters open in it
        self.assertEqual(set(self.tokens), {"key"})

    def test_new_stream_reaches_the_feed_and_transcoding_shows(self):
        server.read_jellyfin()
        self.sessions.append({"Id": "s3", "UserName": "Sam", "NowPlayingItem": {"Id": "i9", "Type": "Movie", "Name": "Arrival", "ProductionYear": 2016, "RunTimeTicks": 100},
                              "PlayState": {"PositionTicks": 0, "PlayMethod": "Transcode"}, "TranscodingInfo": {"IsVideoDirect": False}})
        server.read_jellyfin()
        last = server.LIVE.recent(3)
        self.assertTrue(any(e["source"] == "Jellyfin" and e["kind"] == "play" and e["text"] == "Sam is watching Arrival (2016) (transcoding)" for e in last), last)
        self.assertEqual(server.state["cinema"]["transcoding"], 1)
        self.sessions[0]["PlayState"]["IsPaused"] = True
        server.read_jellyfin()
        self.assertTrue(server.activity("Jellyfin").startswith("Transcoding Arrival (2016) for Sam"))  # the paused one doesn't count

    def test_wrong_key_and_connection_test(self):
        self.assertEqual(server.wizard_test({"what": "app", "app": {"key": "jellyfin", "url": self.app["url"], "api_key": "key"}}),
                         {"ok": True, "version": "10.10.3", "app": "media"})
        self.app["api_key"] = "nope"
        server.read_jellyfin()
        self.assertFalse(server.state["jellyfin"]["ok"])
        self.assertFalse(server.state["cinema"]["ok"])
        self.assertFalse(server.wizard_test({"what": "app", "app": {"key": "jellyfin", "url": self.app["url"], "api_key": "nope"}})["ok"])
