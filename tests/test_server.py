import json
import os
import sys
import tempfile
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
            (wizard(custom_apps=[{"name": "Radarr", "url": "http://x"}]), "used twice"),
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
        self.saved = (dict(server.BY_KIND), server.DOWNLOADER, server.sab_seen)
        server.BY_KIND["nzbget"] = self.app
        server.DOWNLOADER = self.app
        server.sab_seen_set(None)

    def tearDown(self):
        self.srv.shutdown()
        server.BY_KIND.clear()
        server.BY_KIND.update(self.saved[0])
        server.DOWNLOADER = self.saved[1]
        server.sab_seen_set(self.saved[2])

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
