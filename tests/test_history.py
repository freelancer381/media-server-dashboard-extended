import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import history  # noqa: E402

T0 = datetime(2026, 11, 3, 12, 0, 0).timestamp()


class Recorder(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "stats.json")
        self.r = history.Recorder(self.path, clock=lambda: T0)

    def test_minutes_average_and_peak(self):
        for i, v in enumerate([10, 20, 30, 40, 50, 60]):  # a minute of samples every 10 s
            self.r.add({"cpu": v}, T0 + i * 10)
        self.r.add({"cpu": 5}, T0 + 60)  # the next minute closes the first
        s = self.r.series("minute")
        self.assertEqual(s["t"], [T0, T0 + 60])
        self.assertEqual(s["s"]["cpu"], [[35.0, 60.0], [5.0, 5.0]])  # the open minute is shown too
        self.assertEqual(s["step"], 60)

    def test_series_appearing_later_are_padded(self):
        self.r.add({"cpu": 1}, T0)
        self.r.add({"cpu": 1, "ms:Radarr": 3}, T0 + 60)
        self.r.add({"cpu": 1}, T0 + 120)
        self.assertEqual(self.r.series("minute")["s"]["ms:Radarr"][:2], [None, [3.0, 3.0]])

    def test_keeps_only_so_many_points(self):
        step, keep = history.TIERS["minute"]
        for i in range(keep + 10):
            self.r.add({"cpu": i}, T0 + i * step)
        self.assertEqual(len(self.r.tiers["minute"]["t"]), keep)

    def test_saved_and_loaded(self):
        self.r.add({"cpu": 7}, T0)
        self.r.add({"cpu": 9}, T0 + 3600)
        self.r.add_bytes("qbittorrent", 1000, T0)
        self.r.count_events([{"id": 1, "ts": T0 * 1000, "kind": "download", "source": "qBittorrent", "via": "poll", "level": "success", "xp": 10}])
        self.r.save()
        again = history.Recorder(self.path, clock=lambda: T0)
        self.assertEqual(again.series("hour")["s"]["cpu"][0], [7.0, 7.0])
        self.assertEqual(again.downloaded("qbittorrent", 1), 1000)
        self.assertEqual(again.last_event, 1)

    def test_events_counted_once_by_kind_source_and_hour(self):
        ev = [{"id": 1, "ts": T0 * 1000, "kind": "import", "source": "Radarr", "via": "hook", "level": "success", "xp": 15},
              {"id": 2, "ts": T0 * 1000, "kind": "download", "source": "SABnzbd", "via": "poll", "level": "success", "xp": 10}]
        self.r.count_events(ev)
        self.r.count_events(ev)  # the same events again: not counted twice
        d = self.r.day_counts(1)[0]
        self.assertEqual(d["date"], "2026-11-03")
        self.assertEqual(d["kinds"], {"import": 1, "download": 1})
        self.assertEqual(d["via"], {"hook": 1, "poll": 1})
        self.assertEqual(d["hours"][12], 2)
        self.assertEqual(d["xp"], 25)

    def test_downloaded_over_days(self):
        day = 86400
        for i in range(10):
            self.r.add_bytes("rd", 100, T0 - i * day)
        self.assertEqual((self.r.downloaded("rd", 1), self.r.downloaded("rd", 7), self.r.downloaded("rd", 30)), (100, 700, 1000))
        self.r.add_bytes("rd", -5)  # a counter going back never takes bytes away
        self.assertEqual(self.r.downloaded("rd", 1), 100)

    def test_broken_file_starts_empty(self):
        with open(self.path, "w") as f:
            f.write('{"tiers": {"minute": {"t": [1, 2], "s": {"cpu": [[1, 1]]}}}, "days": []}')
        r = history.Recorder(self.path)
        self.assertEqual(r.series("minute")["s"], {})  # a series that doesn't fit its times is dropped
        self.assertEqual(r.days, {})


if __name__ == "__main__":
    unittest.main()
