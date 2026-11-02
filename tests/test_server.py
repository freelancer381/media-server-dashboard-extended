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
