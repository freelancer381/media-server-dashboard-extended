"""Live feed, webhooks and the office's game: standard library only.

Events come from two places: webhooks the apps send (Radarr, Sonarr, Lidarr, Readarr, Prowlarr and
Whisparr's "Webhook" connection, Apprise's json:// for Bazarr and SABnzbd, the Jellyfin webhook plugin,
or any tool posting a small JSON) and what the dashboard notices itself while polling (a download
finished, a service went down, a guest stopped). Each event becomes a chat line from the worker it
belongs to, earns that worker XP, and is pushed to every open page over Server-Sent Events.
"""
import json
import math
import os
import queue
import re
import threading
import time
from collections import deque
from datetime import datetime

# What each kind of event is worth. Problems earn nothing but end the "days without errors" streak.
XP = {"import": 25, "upgrade": 15, "download": 15, "grab": 10, "added": 5, "subtitle": 5, "play": 5,
      "search": 3, "recover": 3, "test": 1, "info": 1, "delete": 0, "health": 0, "error": 0, "badge": 0, "level": 0}
ICONS = {"import": "📦", "upgrade": "⬆️", "download": "📥", "grab": "🛒", "added": "➕", "subtitle": "💬", "play": "▶️",
         "search": "🔎", "recover": "✅", "test": "🧪", "info": "ℹ️", "delete": "🗑️", "health": "⚠️", "error": "🛑", "badge": "🏅", "level": "🎉"}
PROBLEMS = ("health", "error")

BADGES = [
    # id, emoji, name, description, test(player, event, now)
    ("first_import", "🎉", "Erster Import", "Das erste Stück in der Bibliothek", lambda p, e, n: p["counts"].get("import", 0) >= 1),
    ("import_10", "📚", "Sammler", "10 Importe", lambda p, e, n: p["counts"].get("import", 0) >= 10),
    ("import_100", "🏛️", "Archivar", "100 Importe", lambda p, e, n: p["counts"].get("import", 0) >= 100),
    ("import_1000", "🐉", "Hortdrache", "1000 Importe", lambda p, e, n: p["counts"].get("import", 0) >= 1000),
    ("download_100", "🚚", "Spediteur", "100 fertige Downloads", lambda p, e, n: p["counts"].get("download", 0) >= 100),
    ("grab_50", "🛒", "Schnäppchenjäger", "50 Releases geholt", lambda p, e, n: p["counts"].get("grab", 0) >= 50),
    ("night_owl", "🦉", "Nachteule", "Etwas zwischen 0 und 5 Uhr erledigt", lambda p, e, n: e["xp"] > 0 and datetime.fromtimestamp(n).hour < 5),
    ("busy_day", "🔥", "Großkampftag", "20 erledigte Aufgaben an einem Tag", lambda p, e, n: sum(v for k, v in p["today"]["counts"].items() if XP.get(k, 0) > 0) >= 20),
    ("level_5", "⭐", "Profi", "Level 5 erreicht", lambda p, e, n: p["level"] >= 5),
    ("level_10", "🌟", "Legende", "Level 10 erreicht", lambda p, e, n: p["level"] >= 10),
    ("streak_7", "🛡️", "Eine Woche fehlerfrei", "7 Tage ohne Fehler", lambda p, e, n: n - p["clean_since"] >= 7 * 86400),
]
BADGE_INFO = {b[0]: {"emoji": b[1], "name": b[2], "about": b[3]} for b in BADGES}


def level_for(xp):
    """Level 1 at 0 XP, 2 at 50, 3 at 200, 4 at 450, 5 at 800 … (50·(L−1)²)."""
    return int(math.sqrt(max(xp, 0) / 50)) + 1


def xp_for(level):
    return 50 * (level - 1) ** 2


CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean(text, limit=300):
    return CONTROL.sub("", str(text or "")).strip()[:limit]


class Live:
    """The event log, the game and everyone listening. Thread-safe; saves to `data_dir` in the background."""

    def __init__(self, data_dir, enabled=True, keep=300):
        self.events_file = os.path.join(data_dir, "events.json")
        self.game_file = os.path.join(data_dir, "game.json")
        self.enabled = enabled
        self.lock = threading.Lock()
        self.events = deque(self._load(self.events_file, []), maxlen=keep)
        self.game = self._load(self.game_file, {}) or {}
        self.game.setdefault("players", {})
        self.game.setdefault("team", {"since": time.time(), "clean_since": time.time()})
        self.next_id = (self.events[-1]["id"] + 1) if self.events else 1
        self.subscribers = set()
        self.dirty = False
        self.hook_seen = {}  # source -> last webhook time, so polling doesn't report the same thing twice
        threading.Thread(target=self._saver, daemon=True).start()

    # ---------- storage ----------
    @staticmethod
    def _load(path, default):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def _write(self, path, obj):
        tmp = path + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)

    def _saver(self):
        while True:
            time.sleep(5)
            self.flush()

    def flush(self):
        with self.lock:
            if not self.dirty:
                return
            events, game = list(self.events), json.loads(json.dumps(self.game))
            self.dirty = False
        try:
            self._write(self.events_file, events)
            self._write(self.game_file, game)
        except OSError as e:
            print(f"live: could not save: {e}", flush=True)

    # ---------- listeners (SSE) ----------
    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self.lock:
            self.subscribers.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subscribers.discard(q)

    def broadcast(self, name, data):
        self.broadcast_raw(name, json.dumps(data, ensure_ascii=False))

    def broadcast_raw(self, name, text):
        msg = (name, text)
        with self.lock:
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:  # a listener that stopped reading; it reconnects and gets the history again
                self.unsubscribe(q)

    # ---------- the game ----------
    def _player(self, name, now):
        p = self.game["players"].setdefault(name, {"xp": 0, "level": 1, "counts": {}, "badges": [], "clean_since": now,
                                                   "today": {"date": "", "counts": {}, "xp": 0}})
        today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        if p["today"]["date"] != today:
            p["today"] = {"date": today, "counts": {}, "xp": 0}
        return p

    def _score(self, ev, now):
        """Book an event on its worker. Returns follow-up events (level ups, badges)."""
        p = self._player(ev["source"], now)
        kind = ev["kind"]
        p["counts"][kind] = p["counts"].get(kind, 0) + 1
        p["today"]["counts"][kind] = p["today"]["counts"].get(kind, 0) + 1
        if kind in PROBLEMS and ev["level"] in ("warning", "error"):
            p["clean_since"] = now
            self.game["team"]["clean_since"] = now
        before = p["level"]
        p["xp"] += ev["xp"]
        p["today"]["xp"] += ev["xp"]
        p["level"] = level_for(p["xp"])
        extra = []
        if p["level"] > before:
            extra.append(("level", f"erreicht Level {p['level']}! 🎉", {"level": p["level"]}))
        for bid, emoji, name, about, test in BADGES:
            if bid not in p["badges"] and test(p, ev, now):
                p["badges"].append(bid)
                extra.append(("badge", f"bekommt das Abzeichen {emoji} „{name}“ ({about})", {"badge": bid}))
        return extra

    def summary(self):
        """The game for the team board: players by XP, today's totals, the streak."""
        now = time.time()
        today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        with self.lock:
            players = []
            for name, p in self.game["players"].items():
                t = p["today"] if p["today"]["date"] == today else {"counts": {}, "xp": 0}
                players.append({
                    "name": name, "xp": p["xp"], "level": p["level"], "next": xp_for(p["level"] + 1), "base": xp_for(p["level"]),
                    "badges": [{"id": b, **BADGE_INFO.get(b, {"emoji": "🏅", "name": b, "about": ""})} for b in p["badges"]],
                    "today": {"xp": t["xp"], "counts": t["counts"]},
                })
            team_today = {}
            for pl in players:
                for k, v in pl["today"]["counts"].items():
                    team_today[k] = team_today.get(k, 0) + v
            return {
                "enabled": self.enabled, "players": sorted(players, key=lambda x: -x["xp"]),
                "today": {"counts": team_today, "xp": sum(pl["today"]["xp"] for pl in players)},
                "clean_days": int((now - self.game["team"].get("clean_since", now)) // 86400),
            }

    def reset(self):
        with self.lock:
            self.game = {"players": {}, "team": {"since": time.time(), "clean_since": time.time()}}
            self.events.clear()
            self.dirty = True
        self.broadcast("game", self.summary())
        self.broadcast("reset", {})

    # ---------- events ----------
    def emit(self, source, kind, text, level="info", icon=None, via="poll", meta=None):
        source = clean(source, 40) or "Dashboard"
        kind = kind if kind in XP else "info"
        now = time.time()
        if via == "hook":
            self.hook_seen[source] = now
        ev = {"source": source, "kind": kind, "text": clean(text), "level": level if level in ("info", "success", "warning", "error") else "info",
              "icon": clean(icon, 8) or ICONS.get(kind, "•"), "via": via, "xp": XP[kind] if self.enabled else 0, **({"meta": meta} if meta else {})}
        out = []
        with self.lock:
            for k, text2, m in [(None, None, None)] + (self._score(ev, now) if self.enabled else []):
                e = ev if k is None else {"source": source, "kind": k, "text": text2, "level": "success", "icon": ICONS[k], "via": "game", "xp": 0, "meta": m}
                e["id"], e["ts"] = self.next_id, int(now * 1000)
                self.next_id += 1
                self.events.append(e)
                out.append(e)
            self.dirty = True
        for e in out:
            self.broadcast("feed", e)
        if self.enabled:
            self.broadcast("game", self.summary())
        return out[0]

    def recent(self, n=60):
        with self.lock:
            return list(self.events)[-n:]

    def hooked(self, source, within=86400):
        """Has this app sent webhooks lately? Then polling leaves its events to the webhook."""
        return time.time() - self.hook_seen.get(source, 0) < within


# ---------- webhooks ----------
ARR_KEYS = (("movie", "Radarr"), ("series", "Sonarr"), ("artist", "Lidarr"), ("author", "Readarr"), ("book", "Readarr"))


def _quality(body):
    for k in ("release", "movieFile", "episodeFile", "trackFiles", "bookFiles"):
        v = body.get(k)
        if isinstance(v, list):
            v = v[0] if v else {}
        if isinstance(v, dict) and (v.get("quality") or v.get("releaseTitle")):
            q = v.get("quality")
            return q if isinstance(q, str) else ""
    return ""


def _arr_subject(body):
    if isinstance(body.get("movie"), dict):
        m = body["movie"]
        return f"{m.get('title', '?')}" + (f" ({m['year']})" if m.get("year") else "")
    if isinstance(body.get("series"), dict):
        eps = body.get("episodes") or []
        ep = eps[0] if eps and isinstance(eps[0], dict) else {}
        tag = f" S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}" if ep else ""
        more = f" (+{len(eps) - 1})" if len(eps) > 1 else ""
        return f"{body['series'].get('title', '?')}{tag}{more}"
    if isinstance(body.get("artist"), dict):
        albums = body.get("albums") or []
        return body["artist"].get("name", "?") + (f" – {albums[0].get('title')}" if albums and isinstance(albums[0], dict) else "")
    if isinstance(body.get("author"), dict):
        book = body.get("book") or {}
        return body["author"].get("name", "?") + (f" – {book.get('title')}" if isinstance(book, dict) and book.get("title") else "")
    rel = body.get("release") or {}
    return rel.get("releaseTitle") or rel.get("title") or ""


def parse_hook(body, source_hint=""):
    """Turn a webhook body into (source, kind, text, level). Raises ValueError for what isn't understood."""
    if not isinstance(body, dict):
        raise ValueError("JSON object expected")
    hint = clean(source_hint, 40)

    if "eventType" in body:  # the *arr family
        source = hint or clean(body.get("instanceName"), 40) or next((n for k, n in ARR_KEYS if k in body), "") or ("Prowlarr" if "indexer" in json.dumps(body)[:2000].lower() else "Arr")
        et = str(body["eventType"])
        subject = _arr_subject(body)
        q = _quality(body)
        qs = f" in {q}" if q else ""
        if et == "Test":
            return source, "test", "Webhook-Test angekommen 👋", "info"
        if et == "Grab":
            return source, "grab", f"hat {subject or 'ein Release'}{qs} geholt", "info"
        if et in ("Download", "Import", "AlbumImport", "BookImport", "TrackRetag"):
            if body.get("isUpgrade"):
                return source, "upgrade", f"hat {subject} auf{qs or ' eine bessere Qualität'} verbessert", "success"
            return source, "import", f"hat {subject}{qs} importiert", "success"
        if et in ("MovieAdded", "SeriesAdd", "ArtistAdd", "AuthorAdded", "BookAdded"):
            return source, "added", f"hat {subject} neu auf die Liste gesetzt", "info"
        if et.endswith("Delete") or et.endswith("Deleted"):
            return source, "delete", f"hat {subject or 'etwas'} gelöscht", "info"
        if et == "Rename":
            return source, "info", f"hat Dateien von {subject or 'etwas'} umbenannt", "info"
        if et == "Health":
            level = "error" if str(body.get("level", "")).lower() == "error" else "warning"
            return source, "health", clean(body.get("message")) or "meldet ein Problem", level
        if et == "HealthRestored":
            return source, "recover", f"Problem gelöst: {clean(body.get('message'))}".rstrip(": "), "success"
        if et == "ApplicationUpdate":
            return source, "info", f"wurde auf {clean(body.get('newVersion'), 30)} aktualisiert", "info"
        if et == "ManualInteractionRequired":
            return source, "health", f"braucht Hilfe bei {subject or 'einem Download'}", "warning"
        return source, "info", f"{et}: {subject}".rstrip(": "), "info"

    if "NotificationType" in body:  # Jellyfin webhook plugin (see the template in the settings)
        source = hint or clean(body.get("ServerName"), 40) or "Jellyfin"
        nt = str(body["NotificationType"])
        name = clean(body.get("Name") or body.get("ItemName"), 120)
        series = clean(body.get("SeriesName"), 80)
        what = f"{series} – {name}" if series and name and series != name else name or "etwas"
        user = clean(body.get("NotificationUsername") or body.get("Username"), 40)
        if nt == "PlaybackStart":
            return source, "play", f"{user + ' schaut' if user else 'Wiedergabe:'} {what}", "info"
        if nt == "PlaybackStop":
            return source, "info", f"{user + ' hat' if user else 'Wiedergabe von'} {what} {'gestoppt' if user else 'beendet'}", "info"
        if nt == "ItemAdded":
            return source, "import", f"hat {what} in die Bibliothek aufgenommen", "success"
        if nt in ("AuthenticationFailure",):
            return source, "health", f"Fehlgeschlagene Anmeldung{' von ' + user if user else ''}", "warning"
        return source, "info", f"{nt}: {what}", "info"

    if "type" in body and ("message" in body or "title" in body) and "version" in body:  # Apprise json://
        source = hint or "Apprise"
        t = str(body.get("type", "info")).lower()
        title = clean(body.get("title"), 120)
        msg = clean(body.get("message"))
        text = f"{title}: {msg}" if title and msg and title not in msg else msg or title
        low = (title + " " + msg).lower()
        if t == "failure":
            return source, "error", text, "error"
        if t == "warning":
            return source, "health", text, "warning"
        if source.lower() == "bazarr" or "subtitle" in low or "untertitel" in low:
            return source, "subtitle", text, "success"
        if source.lower() == "sabnzbd" and ("complete" in low or "fertig" in low or "finished" in low):
            return source, "download", text, "success"
        return source, "info", text, "success" if t == "success" else "info"

    # Anything else: {"source": "...", "kind": "import", "text": "...", "level": "info", "icon": "🎬"}
    text = body.get("text") or body.get("message") or body.get("title")
    if not text:
        raise ValueError("Unbekanntes Format: erwartet *arr-Webhook, Jellyfin, Apprise JSON oder {source, text}")
    source = hint or clean(body.get("source") or body.get("app"), 40) or "Webhook"
    kind = body.get("kind") if body.get("kind") in XP else "info"
    level = body.get("level") if body.get("level") in ("info", "success", "warning", "error") else "info"
    return source, kind, clean(text), level
