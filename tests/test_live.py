import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import live  # noqa: E402


class HookParsing(unittest.TestCase):
    def test_radarr_import_and_upgrade(self):
        body = {"eventType": "Download", "instanceName": "Radarr", "movie": {"title": "Dune", "year": 2021},
                "movieFile": {"quality": "Bluray-2160p"}, "isUpgrade": False}
        self.assertEqual(live.parse_hook(body), ("Radarr", "import", "imported Dune (2021) in Bluray-2160p", "success"))
        body["isUpgrade"] = True
        self.assertEqual(live.parse_hook(body)[1], "upgrade")

    def test_sonarr_grab_without_instance_name(self):
        body = {"eventType": "Grab", "series": {"title": "Severance"},
                "episodes": [{"seasonNumber": 2, "episodeNumber": 3}, {"seasonNumber": 2, "episodeNumber": 4}],
                "release": {"quality": "WEBDL-1080p", "releaseTitle": "x"}}
        source, kind, text, _ = live.parse_hook(body)
        self.assertEqual((source, kind), ("Sonarr", "grab"))
        self.assertIn("Severance S02E03 (+1)", text)
        self.assertIn("WEBDL-1080p", text)

    def test_source_hint_wins(self):
        self.assertEqual(live.parse_hook({"eventType": "Test"}, "Radarr 4K")[0], "Radarr 4K")

    def test_health(self):
        s, k, t, level = live.parse_hook({"eventType": "Health", "instanceName": "Prowlarr", "level": "error", "message": "Indexer down"})
        self.assertEqual((k, t, level), ("health", "Indexer down", "error"))

    def test_apprise_bazarr_and_sab(self):
        self.assertEqual(live.parse_hook({"version": "1.0", "title": "Bazarr", "message": "Dune: German subtitles downloaded", "type": "info"}, "Bazarr")[1], "subtitle")
        self.assertEqual(live.parse_hook({"version": "1.0", "title": "SABnzbd: Job finished", "message": "Dune.2021 completed", "type": "success"}, "SABnzbd")[1], "download")
        self.assertEqual(live.parse_hook({"version": "1.0", "title": "SABnzbd", "message": "Job failed", "type": "failure"}, "SABnzbd")[1:], ("error", "SABnzbd: Job failed", "error"))

    def test_jellyfin(self):
        s, k, t, _ = live.parse_hook({"NotificationType": "PlaybackStart", "Name": "Pilot", "SeriesName": "Lost", "NotificationUsername": "Alex"})
        self.assertEqual((s, k), ("Jellyfin", "play"))
        self.assertEqual(t, "Alex is watching Lost – Pilot")

    def test_generic_and_garbage(self):
        self.assertEqual(live.parse_hook({"source": "Backup", "text": "done", "kind": "download", "level": "success"}),
                         ("Backup", "download", "done", "success"))
        with self.assertRaises(ValueError):
            live.parse_hook({"foo": 1})
        with self.assertRaises(ValueError):
            live.parse_hook([1, 2])

    def test_text_is_cleaned_and_capped(self):
        _, _, text, _ = live.parse_hook({"text": "a\x00b\x1b" + "x" * 1000})
        self.assertTrue(text.startswith("ab"))
        self.assertLessEqual(len(text), 300)


class Game(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.live = live.Live(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_levels(self):
        self.assertEqual([live.level_for(x) for x in (0, 49, 50, 199, 200, 800)], [1, 1, 2, 2, 3, 5])

    def test_xp_level_up_and_badges(self):
        q = self.live.subscribe()
        self.live.emit("Radarr", "import", "imported X", "success")
        self.live.emit("Radarr", "import", "imported Y", "success")
        names = []
        while not q.empty():
            names.append(q.get_nowait()[0])
        self.assertIn("feed", names)
        self.assertIn("game", names)
        kinds = [e["kind"] for e in self.live.recent()]
        self.assertEqual(kinds.count("badge"), 1)  # "first import"
        self.assertEqual(kinds.count("level"), 1)  # 50 XP: level 2
        p = self.live.summary()["players"][0]
        self.assertEqual((p["name"], p["xp"], p["level"]), ("Radarr", 50, 2))
        self.assertEqual(p["today"]["counts"]["import"], 2)

    def test_problems_reset_the_streak_and_earn_nothing(self):
        self.live.game["team"]["clean_since"] = time.time() - 10 * 86400
        self.assertEqual(self.live.summary()["clean_days"], 10)
        ev = self.live.emit("Sonarr", "health", "broken", "error")
        self.assertEqual(ev["xp"], 0)
        self.assertEqual(self.live.summary()["clean_days"], 0)

    def test_saved_and_loaded(self):
        self.live.emit("Bazarr", "subtitle", "Subtitles", "success")
        self.live.flush()
        again = live.Live(self.dir.name)
        self.assertEqual(again.recent()[-1]["text"], "Subtitles")
        self.assertEqual(again.summary()["players"][0]["xp"], live.XP["subtitle"])
        self.assertGreater(again.next_id, again.recent()[-1]["id"])

    def test_disabled_game_still_logs(self):
        off = live.Live(self.dir.name, enabled=False)
        ev = off.emit("Radarr", "import", "x", "success")
        self.assertEqual(ev["xp"], 0)
        self.assertEqual(off.summary()["players"], [])

    def test_slow_listener_is_dropped(self):
        q = self.live.subscribe()
        for i in range(250):
            self.live.broadcast("x", i)
        self.assertNotIn(q, self.live.subscribers)

    def test_hooked(self):
        self.assertFalse(self.live.hooked("Radarr"))
        self.live.emit("Radarr", "grab", "x", via="hook")
        self.assertTrue(self.live.hooked("Radarr"))

    def test_night_owl(self):
        with mock.patch("live.datetime") as dt:
            dt.fromtimestamp.side_effect = lambda ts: __import__("datetime").datetime(2026, 1, 1, 3, 0)
            self.live.emit("Radarr", "grab", "at night", "info")
        self.assertIn("night_owl", self.live.game["players"]["Radarr"]["badges"])


if __name__ == "__main__":
    unittest.main()
