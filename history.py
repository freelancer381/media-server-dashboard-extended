"""Long-term numbers for the statistics page: standard library only.

The dashboard itself keeps five minutes of samples in memory. This keeps averages and peaks for longer, in
three resolutions (a point per minute for a day, per hour for 90 days, per day for two years), plus counts
per day: what was downloaded by which client, and what happened in the live feed. It is saved to
`stats.json` in the data folder now and then and when the dashboard stops.
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta

TIERS = {"minute": (60, 1440), "hour": (3600, 90 * 24), "day": (86400, 730)}  # seconds per point, points kept
KEEP_DAYS = 400
SERIES_MAX = 64  # app latencies are series of their own; a cap keeps a renamed app from growing the file forever


def day_of(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


class Recorder:
    def __init__(self, path, clock=time.time):
        self.path = path
        self.clock = clock
        self.lock = threading.Lock()
        data = self._load()
        # tier -> {"t": [bucket starts], "s": {series: [[avg, max] or None, ...]}}
        self.tiers = {k: self._tier(data.get("tiers", {}).get(k)) for k in TIERS}
        self.open = {k: {"start": None, "sums": {}} for k in TIERS}  # the bucket being filled
        self.days = data.get("days") if isinstance(data.get("days"), dict) else {}
        self.last_event = int(data.get("last_event") or 0)
        self.since = float(data.get("since") or self.clock())
        self.dirty = False

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _tier(raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("t"), list) or not isinstance(raw.get("s"), dict):
            return {"t": [], "s": {}}
        n = len(raw["t"])
        return {"t": list(raw["t"]), "s": {k: list(v) for k, v in raw["s"].items() if isinstance(v, list) and len(v) == n}}

    # ---------- samples ----------
    def add(self, values, now=None):
        """One sample of several series, e.g. {"cpu": 12.5, "rx": 1e6}. None values are skipped."""
        now = self.clock() if now is None else now
        with self.lock:
            for name, (step, keep) in TIERS.items():
                b = self.open[name]
                start = int(now // step * step)
                if b["start"] is not None and start != b["start"]:
                    self._close(name)
                b["start"] = start
                for k, v in values.items():
                    if v is None:
                        continue
                    s = b["sums"].setdefault(k, [0.0, 0, float("-inf")])
                    s[0] += float(v)
                    s[1] += 1
                    s[2] = max(s[2], float(v))
            self.dirty = True

    def _close(self, name):
        """The bucket is complete: its average and peak become a point."""
        b, tier = self.open[name], self.tiers[name]
        if b["start"] is None or not b["sums"]:
            b["sums"] = {}
            return
        if tier["t"] and tier["t"][-1] >= b["start"]:  # the clock went back: start over rather than mix them up
            b["sums"] = {}
            return
        tier["t"].append(b["start"])
        n = len(tier["t"])
        for k, (total, count, peak) in b["sums"].items():
            if k not in tier["s"]:
                if len(tier["s"]) >= SERIES_MAX:
                    continue
                tier["s"][k] = [None] * (n - 1)
            tier["s"][k].append([round(total / count, 2), round(peak, 2)])
        for k, v in tier["s"].items():
            if len(v) < n:
                v.append(None)
        keep = TIERS[name][1]
        if n > keep:
            cut = n - keep
            tier["t"] = tier["t"][cut:]
            for k in list(tier["s"]):
                tier["s"][k] = tier["s"][k][cut:]
                if not any(tier["s"][k]):
                    del tier["s"][k]  # nothing left of an app that is gone
        b["sums"] = {}

    def series(self, tier, since=None, names=None):
        """Points of one resolution, with the bucket being filled at the end so the newest minute shows too."""
        with self.lock:
            t = self.tiers[tier]
            idx = 0
            if since is not None:
                idx = next((i for i, x in enumerate(t["t"]) if x >= since), len(t["t"]))
            out = {"step": TIERS[tier][0], "t": t["t"][idx:], "s": {k: v[idx:] for k, v in t["s"].items() if names is None or k in names}}
            b = self.open[tier]
            if b["start"] is not None and b["sums"] and (not out["t"] or out["t"][-1] < b["start"]):
                out["t"] = out["t"] + [b["start"]]
                for k in set(out["s"]) | set(b["sums"]):
                    if names is not None and k not in names:
                        continue
                    s = b["sums"].get(k)
                    out["s"].setdefault(k, [None] * (len(out["t"]) - 1))
                    out["s"][k] = out["s"][k] + [[round(s[0] / s[1], 2), round(s[2], 2)] if s else None]
            return out

    # ---------- counts per day ----------
    def _day(self, now):
        d = self.days.setdefault(day_of(now), {})
        if len(self.days) > KEEP_DAYS:
            for old in sorted(self.days)[:-KEEP_DAYS]:
                del self.days[old]
        return d

    def add_bytes(self, client, n, now=None):
        """Bytes a download client fetched; for the days the client doesn't count itself."""
        if not n or n < 0:
            return
        now = self.clock() if now is None else now
        with self.lock:
            dl = self._day(now).setdefault("dl", {})
            dl[client] = dl.get(client, 0) + int(n)
            self.dirty = True

    def downloaded(self, client, days, now=None):
        """Bytes in the last `days` days, today included."""
        now = self.clock() if now is None else now
        today = datetime.fromtimestamp(now)
        with self.lock:
            return sum((self.days.get((today - timedelta(days=i)).strftime("%Y-%m-%d"), {}).get("dl") or {}).get(client, 0) for i in range(days))

    def count_events(self, events):
        """New live feed events (by id) into the counts of their day: by kind, by app, polled or by webhook, and by hour."""
        with self.lock:
            for e in events:
                if e.get("id", 0) <= self.last_event:
                    continue
                self.last_event = e["id"]
                ts = (e.get("ts") or 0) / 1000 or self.clock()
                d = self._day(ts)
                for key, val in (("kinds", e.get("kind")), ("sources", e.get("source")), ("via", e.get("via")), ("levels", e.get("level"))):
                    if val:
                        c = d.setdefault(key, {})
                        if key == "sources" and val not in c and len(c) >= SERIES_MAX:
                            continue
                        c[val] = c.get(val, 0) + 1
                hours = d.setdefault("hours", [0] * 24)
                hours[datetime.fromtimestamp(ts).hour] += 1
                d["xp"] = d.get("xp", 0) + int(e.get("xp") or 0)
                self.dirty = True

    def day_counts(self, days, now=None):
        now = self.clock() if now is None else now
        today = datetime.fromtimestamp(now)
        with self.lock:
            out = []
            for i in range(days - 1, -1, -1):
                key = (today - timedelta(days=i)).strftime("%Y-%m-%d")
                out.append({"date": key, **json.loads(json.dumps(self.days.get(key, {})))})
            return out

    # ---------- saving ----------
    def save(self, force=False):
        with self.lock:
            if not (self.dirty or force):
                return
            data = {"since": self.since, "last_event": self.last_event, "tiers": self.tiers, "days": self.days}
            text = json.dumps(data, separators=(",", ":"))
            self.dirty = False
        tmp = self.path + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, self.path)
