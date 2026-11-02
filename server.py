#!/usr/bin/env python3
"""Media server dashboard and 3D office for a Proxmox homelab. Standard library only.

Runs in its own LXC (or any Linux box) and watches everything from the outside over the network:
the Proxmox host through its API (read-only token), and Radarr, Sonarr, Prowlarr, Bazarr, SABnzbd and any
other app you add, wherever they run (other LXCs, Docker, VMs). API keys and the Proxmox token stay on the
server; only the fields picked out below reach the browser.

    python3 server.py          run; without a finished setup it opens the setup wizard at /setup
    python3 server.py --demo   made-up data, for trying it out or taking screenshots

Settings live in $MSD_DATA/config.json (default: next to this file), written by the setup wizard.
"""
import hashlib
import hmac
import html
import http.cookies
import json
import os
import re
import secrets
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("MSD_DATA") or HERE
DEMO = "--demo" in sys.argv
CONFIG_FILE = os.path.join(DATA, "config.json")
SECRET_FILE = os.path.join(DATA, ".secret")
SETUP_TOKEN_FILE = os.path.join(DATA, "setup-token.txt")


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {}
    except ValueError as e:
        sys.exit(f"{CONFIG_FILE} is not valid JSON: {e}")


CONFIG = load_config()
PORT = int(CONFIG.get("port") or 8900)
BIND = CONFIG.get("bind") or "0.0.0.0"
TITLE = CONFIG.get("title") or "Media Server Dashboard"
AUTH = CONFIG.get("auth") or {}
SETUP_DONE = bool(AUTH.get("user") and AUTH.get("hash"))
# Requests from these addresses are a reverse proxy (Caddy in the LXC): the real client is in X-Forwarded-For.
TRUSTED_PROXIES = set(CONFIG.get("trusted_proxies") or [])
INTERNET_CHECK = CONFIG.get("internet_check") or "1.1.1.1:443"
WATCH_GUESTS = [str(g) for g in CONFIG.get("watch_guests") or []]
STORAGES = CONFIG.get("storages") or []  # empty: every active storage on the node
PVE = CONFIG.get("proxmox") or {}
HOME = CONFIG.get("home") or {}

# The apps the dashboard knows by heart. Anything else is a custom app: "arr" (Lidarr, Readarr, Whisparr...)
# or "generic" (any HTTP API with a status URL).
BUILTIN = {
    "radarr": {"name": "Radarr", "kind": "radarr", "api": "v3", "port": 7878},
    "sonarr": {"name": "Sonarr", "kind": "sonarr", "api": "v3", "port": 8989},
    "prowlarr": {"name": "Prowlarr", "kind": "prowlarr", "api": "v1", "port": 9696},
    "bazarr": {"name": "Bazarr", "kind": "bazarr", "api": "", "port": 6767},
    "sabnzbd": {"name": "SABnzbd", "kind": "sabnzbd", "api": "", "port": 8080},
}


def make_app(key, a, base=None):
    base = base or {}
    url = (a.get("url") or "").rstrip("/")
    u = urllib.parse.urlparse(url)
    return {
        "key": key, "name": a.get("name") or base.get("name") or key, "kind": base.get("kind") or a.get("kind") or "generic",
        "api": a.get("api_version") or base.get("api", "v1"), "url": url, "api_key": a.get("api_key") or "",
        "host": u.hostname, "port": u.port or (443 if u.scheme == "https" else 80), "verify": a.get("verify_tls", True),
        "auth": a.get("auth") or ("query" if base.get("kind") == "sabnzbd" else "header"),
        "auth_name": a.get("auth_name") or ("X-API-KEY" if base.get("kind") == "bazarr" else "X-Api-Key"),
        "health_path": a.get("health_path") or "/",
    }


def configured_apps(cfg):
    apps = []
    for key, base in BUILTIN.items():
        a = (cfg.get("apps") or {}).get(key) or {}
        if a.get("url") and a.get("api_key") and a.get("enabled", True):
            apps.append(make_app(key, a, base))
    for i, a in enumerate(cfg.get("custom_apps") or []):
        if a.get("url") and a.get("name") and a.get("enabled", True):
            apps.append(make_app("custom-" + re.sub(r"[^a-z0-9]+", "-", a["name"].lower()).strip("-"), a))
    return apps


APPS = configured_apps(CONFIG)
BY_KIND = {a["kind"]: a for a in APPS if a["kind"] in BUILTIN}

VENDOR = {"/vendor/three.module.min.js": "three.module.min.js", "/vendor/OrbitControls.js": "OrbitControls.js", "/vendor/RoundedBoxGeometry.js": "RoundedBoxGeometry.js"}  # the 3D office's library, served as-is
POSTER_HOSTS = ("https://image.tmdb.org/", "https://artworks.thetvdb.com/")
poster_cache = {}
poster_lock = threading.Lock()
MAX_ATTEMPTS = 8
LOCKOUT = 5 * 60

TICK = 2            # seconds between system samples
KEEP = 150          # samples kept: 5 minutes

history = {k: deque(maxlen=KEEP) for k in ("t", "cpu", "mem", "rx", "tx", "disk", "sab")}
state = {"system": {}, "services": [], "cinema": {"enabled": False, "profiles": [], "streams": [], "watched": [], "missing_drives": []},
         "sab": {"ok": False}, "radarr": {"ok": False}, "sonarr": {"ok": False}, "prowlarr": {"ok": False}, "bazarr": {"ok": False},
         "custom": {}, "activity": {}, "network": {}, "disks": [], "proxmox": {"ok": None, "guests": []}}
for _k in BUILTIN:  # apps that aren't set up say so, instead of looking broken
    if _k not in BY_KIND:
        state["sab" if _k == "sabnzbd" else _k] = {"ok": False, "off": True}


# ---------- HTTP ----------
def ssl_context(verify=True, ca_file=None):
    if verify is False:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()


DEFAULT_SSL = ssl_context()
INSECURE_SSL = ssl_context(False)
FAIL = (OSError, ValueError, KeyError, TypeError, IndexError, urllib.error.URLError)


def http_get(url, headers=None, timeout=6, context=None, raw=False):
    req = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=context or DEFAULT_SSL) as r:
        return r.read(65536) if raw else json.load(r)


def app_url(a, path):
    """Full URL and headers for a request to one app's API. `path` is relative to the API root."""
    headers = {}
    if a["kind"] == "sabnzbd":
        return f"{a['url']}/api?output=json&apikey={urllib.parse.quote(a['api_key'])}&mode={path}", headers
    if a["kind"] in ("radarr", "sonarr", "prowlarr", "arr"):
        url = f"{a['url']}/api/{a['api']}/{path}"
    elif a["kind"] == "bazarr":
        url = f"{a['url']}/api/{path}"
    else:
        url = a["url"] + "/" + path.lstrip("/")
    if a["api_key"] and a["auth"] == "query":
        url += ("&" if "?" in url else "?") + f"{urllib.parse.quote(a['auth_name'])}={urllib.parse.quote(a['api_key'])}"
    elif a["api_key"] and a["auth"] == "header":
        headers[a["auth_name"]] = a["api_key"]
    return url, headers


def app_get(a, path, timeout=6, raw=False):
    """GET one of the apps' APIs. Raises on any failure; the callers catch it."""
    url, headers = app_url(a, path)
    return http_get(url, headers, timeout=timeout, context=DEFAULT_SSL if a["verify"] else INSECURE_SSL, raw=raw)


def reachable(host, port, timeout=3):
    """Milliseconds to open a TCP connection, or None. Works unprivileged, unlike ping."""
    started = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.time() - started) * 1000
    except (OSError, TypeError):
        return None


# ---------- the Proxmox host, every couple of seconds ----------
def pve_get(p, path, timeout=5):
    ctx = ssl_context(p.get("verify_tls", True), p.get("ca_file") or None)
    url = p["url"].rstrip("/") + "/api2/json/" + path.lstrip("/")
    return http_get(url, {"Authorization": f"PVEAPIToken={p['token_id']}={p['token_secret']}"}, timeout=timeout, context=ctx)["data"]


pve = lambda path, timeout=5: pve_get(PVE, path, timeout)


class Counter:
    """Turns ever-growing byte counters into a rate. Proxmox refreshes guest counters only every ~10 s,
    so a rate is worked out when the total moves and held until it moves again."""

    def __init__(self):
        self.total = self.at = None
        self.rate = 0.0

    def update(self, total, now):
        if self.total is None or total < self.total:  # first reading, or a guest restarted and its counter reset
            self.total, self.at = total, now
            return self.rate
        if total != self.total:
            self.rate = (total - self.total) / max(now - self.at, 0.5)
            self.total, self.at = total, now
        elif now - self.at > 25:  # nothing moved for a few refreshes: really idle
            self.rate = 0.0
        return self.rate


counters = {k: Counter() for k in ("rx", "tx", "disk")}
guest_counters = {}  # vmid -> {"rx": Counter, "tx": Counter}, for the start page tiles


def read_host():
    node = PVE["node"]
    st = pve(f"nodes/{node}/status")
    guests = pve("cluster/resources?type=vm")
    now = time.time()
    mine = [g for g in guests if g.get("node") == node]
    running = [g for g in mine if g.get("status") == "running"]
    # Host traffic as the sum of its guests': what the media apps download and serve.
    rx = counters["rx"].update(sum(g.get("netin", 0) for g in running), now)
    tx = counters["tx"].update(sum(g.get("netout", 0) for g in running), now)
    disk = counters["disk"].update(sum(g.get("diskread", 0) + g.get("diskwrite", 0) for g in running), now)

    mem = st.get("memory") or {}
    swap = st.get("swap") or {}
    total = mem.get("total") or 1
    cpu = float(st.get("cpu") or 0) * 100
    wait = float(st.get("wait") or 0) * 100
    info = st.get("cpuinfo") or {}
    state["system"].update(
        host=node, cpu_name=info.get("model", ""), cores=int(info.get("cpus") or 1),
        pve=st.get("pveversion", ""), kernel=(st.get("current-kernel") or {}).get("release") or st.get("kversion", ""),
        booted=int((now - float(st.get("uptime") or 0)) * 1000), speed_limit=100,
        cpu={"user": round(cpu - wait, 1), "system": round(wait, 1), "idle": round(100 - cpu, 1)},
        load=[float(x) for x in st.get("loadavg") or (0, 0, 0)],
        memory={"total": total, "used": mem.get("used", 0), "free_level": round((mem.get("free", total) / total) * 100),
                "swap_total": swap.get("total", 0), "swap_used": swap.get("used", 0)},
    )
    def rates(g):
        c = guest_counters.setdefault(g.get("vmid"), {"rx": Counter(), "tx": Counter()})
        if g.get("status") != "running":
            return 0, 0
        return round(c["rx"].update(g.get("netin", 0), now)), round(c["tx"].update(g.get("netout", 0), now))
    state["proxmox"] = {"ok": True, "guests": sorted(({
        "id": g.get("vmid"), "name": g.get("name"), "type": g.get("type"), "status": g.get("status"),
        "cpu": round(float(g.get("cpu") or 0) * 100, 1), "mem": g.get("mem", 0), "maxmem": g.get("maxmem", 0),
        "uptime": g.get("uptime", 0), **dict(zip(("rx", "tx"), rates(g))),
    } for g in mine), key=lambda g: g["id"] or 0)}
    history["t"].append(int(now * 1000))
    history["cpu"].append(round(cpu))
    history["mem"].append(round(mem.get("used", 0) / total * 100, 1))
    history["rx"].append(round(rx))
    history["tx"].append(round(tx))
    history["disk"].append(round(disk))
    history["sab"].append(state["sab"].get("speed", 0))


def system_loop():
    while True:
        started = time.time()
        try:
            read_host()
        except FAIL as e:
            state["proxmox"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200], "guests": state["proxmox"].get("guests", [])}
        time.sleep(max(0.1, TICK - (time.time() - started)))


def looped(every, fn):
    def run():
        while True:
            try:
                fn()
            except Exception as e:  # one bad reading must not stop the dashboard
                print(f"{fn.__name__}: {type(e).__name__}: {e}", flush=True)
            time.sleep(every)
    threading.Thread(target=run, daemon=True).start()


def read_disks():
    disks = []
    for s in pve(f"nodes/{PVE['node']}/storage"):
        if not s.get("active") or not s.get("total"):
            continue
        if STORAGES and s.get("storage") not in STORAGES:
            continue
        disks.append({"name": s["storage"], "total": s["total"], "free": s.get("avail", s["total"] - s.get("used", 0))})
    state["disks"] = disks


def read_network():
    lan = ""
    try:  # which address this container uses to reach the Proxmox host (nothing is actually sent)
        target = urllib.parse.urlparse(PVE["url"]).hostname if PVE else "1.1.1.1"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((target, 9))
            lan = s.getsockname()[0]
    except OSError:
        pass
    host, _, port = INTERNET_CHECK.rpartition(":")
    ms = reachable(host, int(port))
    state["network"] = {"lan": lan, "vpn": False, "online": ms is not None, "ping": round(ms, 1) if ms is not None else None}


# ---------- the start page next to the office ----------
def tile_target(t):
    """host, port to check for a tile: its own "IP:port" if given, otherwise taken from its URL."""
    check = (t.get("check") or "").strip()
    if check:
        host, _, port = check.rpartition(":") if re.search(r":\d+$", check) else (check, "", "")
        return host.strip("[]"), int(port or 80)
    u = urllib.parse.urlparse(t.get("url") or "")
    return u.hostname, u.port or (443 if u.scheme == "https" else 80)


home_checks = {}


def read_home():
    out = {}
    for i, t in enumerate(HOME.get("tiles") or []):
        host, port = tile_target(t)
        ms = reachable(host, port, timeout=2) if host else None
        out[i] = {"up": ms is not None, "ms": round(ms) if ms is not None else None}
    home_checks.clear()
    home_checks.update(out)


def home_payload():
    if not HOME.get("enabled"):
        return {"enabled": False}
    guests = {str(g["id"]): g for g in state["proxmox"].get("guests", [])}
    tiles = []
    for i, t in enumerate(HOME.get("tiles") or []):
        g = guests.get(str(t.get("guest") or ""))
        tiles.append({
            "name": t.get("name"), "url": t.get("url") or "", "icon": t.get("icon") or "", "check": "%s:%s" % tile_target(t),
            **home_checks.get(i, {"up": None, "ms": None}),
            "guest": {k: g.get(k) for k in ("id", "name", "type", "status", "cpu", "mem", "maxmem", "rx", "tx", "uptime")} if g else None,
        })
    return {"enabled": True, "side": HOME.get("side", "right"), "layout": HOME.get("layout", "grid"), "tiles": tiles}


# ---------- apps ----------
def read_services():
    """Does each app accept connections on its port?"""
    state["services"] = [{"name": a["name"], "key": a["key"], "up": reachable(a["host"], a["port"]) is not None} for a in APPS]


def poster(item):
    for img in item.get("images", []):
        if img.get("coverType") == "poster" and str(img.get("remoteUrl", "")).startswith("https://"):
            return img["remoteUrl"]
    return None


arr_slow = {}


def read_arr(kind):
    a = BY_KIND.get(kind)
    if not a:
        return
    movies = kind == "radarr"
    get = lambda p: app_get(a, p)
    include = "includeMovie=true" if movies else "includeSeries=true&includeEpisode=true"
    try:
        slow = arr_slow.get(kind)
        if not slow or time.time() - slow["at"] > 60:
            start = datetime.now(timezone.utc)
            end = start + timedelta(days=14)
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            slow = {
                "at": time.time(), "status": get("system/status"), "health": get("health"), "roots": get("rootfolder"),
                "missing": get("wanted/missing?pageSize=1"),
                "items": get("movie" if movies else "series"),
                "history": get(f"history?pageSize=8&eventType=3&sortKey=date&sortDirection=descending&{include}"),
                "calendar": get(f"calendar?start={start.strftime(fmt)}&end={end.strftime(fmt)}" + ("" if movies else "&includeSeries=true")),
            }
            arr_slow[kind] = slow
    except FAIL:
        state[kind] = {"ok": False}
        arr_slow.pop(kind, None)
        return

    ep = lambda e: f"S{e.get('seasonNumber', 0):02d}E{e.get('episodeNumber', 0):02d}"

    items = slow["items"]
    if movies:
        have = [m for m in items if m.get("hasFile")]
        have.sort(key=lambda m: (m.get("movieFile") or {}).get("dateAdded") or m.get("added") or "", reverse=True)
        recent = [{
            "title": m.get("title"), "year": m.get("year"), "poster": poster(m), "size": m.get("sizeOnDisk", 0),
            "quality": ((m.get("movieFile") or {}).get("quality") or {}).get("quality", {}).get("name"),
            "when": (m.get("movieFile") or {}).get("dateAdded") or m.get("added"),
        } for m in have[:8]]
        library = {"total": len(items), "have": len(have), "size": sum(m.get("sizeOnDisk", 0) for m in items)}
    else:
        stat = lambda s, k: (s.get("statistics") or {}).get(k, 0)
        recent = [{
            "title": (h.get("series") or {}).get("title") or h.get("sourceTitle"),
            "sub": ep(h["episode"]) + " " + h["episode"].get("title", "") if h.get("episode") else "",
            "poster": poster(h.get("series") or {}),
            "quality": ((h.get("quality") or {}).get("quality") or {}).get("name"), "when": h.get("date"),
        } for h in slow["history"].get("records", [])]
        library = {"total": len(items), "have": sum(stat(s, "episodeFileCount") for s in items), "episodes": sum(stat(s, "episodeCount") for s in items), "size": sum(stat(s, "sizeOnDisk") for s in items)}

    upcoming = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for c2 in slow["calendar"]:
        if c2.get("hasFile"):
            continue
        if movies:
            when = next((d for d in (c2.get("digitalRelease"), c2.get("physicalRelease"), c2.get("inCinemas")) if d and d >= now), None)
            if when:
                upcoming.append({"kind": "Movie", "title": c2.get("title"), "when": when})
        elif c2.get("airDateUtc"):
            upcoming.append({"kind": "Episode", "title": f"{(c2.get('series') or {}).get('title', '')} {ep(c2)}".strip(), "when": c2["airDateUtc"]})

    state[kind] = {
        "ok": True, "version": slow["status"].get("version"), "library": library, "missing": slow["missing"].get("totalRecords", 0),
        "recent": recent, "upcoming": upcoming,
        "health": [{"level": h.get("type"), "message": h.get("message")} for h in slow["health"]],
        "roots": [{"path": r.get("path"), "ok": r.get("accessible", True)} for r in slow["roots"]],
    }


def read_radarr():
    read_arr("radarr")


def read_sonarr():
    read_arr("sonarr")


def read_prowlarr():
    a = BY_KIND.get("prowlarr")
    if not a:
        return
    try:
        indexers = app_get(a, "indexer")
        state["prowlarr"] = {
            "ok": True, "version": app_get(a, "system/status").get("version"), "indexers": len(indexers),
            "enabled": sum(1 for i in indexers if i.get("enable")),
            "health": [{"level": h.get("type"), "message": h.get("message")} for h in app_get(a, "health")],
        }
    except FAIL:
        state["prowlarr"] = {"ok": False}


def read_bazarr():
    a = BY_KIND.get("bazarr")
    if not a:
        return
    try:
        health = app_get(a, "system/health")["data"]
        state["bazarr"] = {"ok": True, "health": [{"level": "warning", "message": f"{h.get('object')}: {h.get('issue')}"} for h in health]}
    except FAIL:
        state["bazarr"] = {"ok": False}


def check_custom(a):
    """One look at a custom app: is its API answering, and (for *arr apps) what does its health page say?"""
    if a["kind"] == "arr":
        status = app_get(a, "system/status")
        return {"ok": True, "version": status.get("version"), "health": [{"level": h.get("type"), "message": h.get("message")} for h in app_get(a, "health")]}
    app_get(a, a["health_path"], raw=True)  # any 2xx is fine; errors raise
    return {"ok": True, "health": []}


def read_custom():
    out = {}
    for a in APPS:
        if a["kind"] not in ("arr", "generic"):
            continue
        try:
            out[a["key"]] = check_custom(a)
        except FAIL:
            out[a["key"]] = {"ok": False}
    state["custom"] = out


def utc_ms(text):
    return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000


download_titles = {}  # SABnzbd download id -> the movie or episode it is, from Radarr and Sonarr
download_owner = {}  # SABnzbd download id -> "Radarr" or "Sonarr"


def read_activity():
    """What the *arr apps and Bazarr are busy with right now, for the office."""
    act, titles, owner = {}, {}, {}
    for a in APPS:
        if a["kind"] not in ("radarr", "sonarr", "prowlarr", "arr"):
            continue
        get = lambda p: app_get(a, p)
        try:
            jobs = [x.get("commandName") or x.get("name") for x in get("command") if x.get("status") in ("started", "queued")]
            if a["kind"] == "prowlarr":
                last = (get("history?pageSize=1&sortKey=date&sortDirection=descending").get("records") or [None])[0]
                if not jobs and last and time.time() * 1000 - utc_ms(last["date"]) < 90 * 1000:
                    query = (last.get("data") or {}).get("query")
                    jobs = [f"Searching for {query}" if query else "Checking RSS feeds"]
            elif a["kind"] in ("radarr", "sonarr"):
                # Their queue says which movie or episode each download is, so SABnzbd's file names can be shown properly.
                movies = a["kind"] == "radarr"
                q = get("queue?pageSize=500&" + ("includeMovie=true" if movies else "includeSeries=true&includeEpisode=true"))
                for r in q.get("records", []):
                    if r.get("downloadId"):
                        titles[r["downloadId"].lower()] = nice_title(r, movies)
                        owner[r["downloadId"].lower()] = a["name"]
                waiting = q.get("totalRecords", 0)
                if waiting and not jobs:
                    jobs = [f"Waiting on {waiting} download{'s' if waiting > 1 else ''}"]
        except FAIL:
            continue
        act[a["name"]] = jobs[0] if jobs else None
    b = BY_KIND.get("bazarr")
    if b:
        try:
            tasks = app_get(b, "system/tasks")["data"]
            act[b["name"]] = next((t["name"] for t in tasks if t.get("job_running")), None)
        except FAIL:
            pass
    state["activity"] = act
    download_titles.clear()
    download_titles.update(titles)
    download_owner.clear()
    download_owner.update(owner)


def up_next(name, n=5):
    """The next downloads in SABnzbd's order, for the popup on a service box. Radarr and Sonarr get only their own."""
    if name not in ("SABnzbd", "Radarr", "Sonarr"):
        return []
    sab = state["sab"]
    out = []
    for s in sab.get("queue", []) if sab.get("ok") else []:
        sid = (s.get("id") or "").lower()
        if name != "SABnzbd" and download_owner.get(sid) != name:
            continue
        out.append({"title": download_titles.get(sid) or tidy(s["title"]), "pct": round(s["pct"]), "size": s.get("size")})
        if len(out) == n:
            break
    return out


def nice_title(r, movie):
    if movie:
        m = r.get("movie") or {}
        return f"{m['title']} ({m['year']})" if m.get("title") and m.get("year") else m.get("title") or tidy(r.get("title", ""))
    series, ep = r.get("series") or {}, r.get("episode") or {}
    if series.get("title") and ep:
        return f"{series['title']} S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}"
    return tidy(r.get("title", ""))


def tidy(name):
    """A release file name made readable: 'Atlantis.The.Lost.Empire.2001.1080p...' becomes 'Atlantis The Lost Empire (2001)'."""
    n = re.sub(r"[._]+", " ", name).strip()
    ep = re.match(r"(.+?)\s+(S\d\d?E\d\d?)\b", n, re.I)
    if ep:
        return f"{ep[1].strip()} {ep[2].upper()}"
    year = re.match(r"(.+?)\s*[\(\[]?((?:19|20)\d\d)[\)\]]?(?:\s|$)", n)
    return f"{year[1].strip()} ({year[2]})" if year else n


def activity(name):
    """The task an app is busy with, or None when it has nothing to do."""
    if name == "SABnzbd":
        sab = state["sab"]
        if not sab.get("ok") or sab["paused"] or not (sab["speed"] > 0 or sab["queueTotal"]):
            return None
        top = sab["queue"][0] if sab["queue"] else None
        if not top:
            return "Downloading"
        title = download_titles.get((top.get("id") or "").lower()) or tidy(top["title"])
        return f"Downloading {title} · {round(top['pct'])}%"
    return state["activity"].get(name)


def read_sab():
    a = BY_KIND.get("sabnzbd")
    if not a:
        return
    try:
        q = app_get(a, "queue&limit=60")["queue"]  # enough to find the next few of Radarr's and of Sonarr's
        failed = app_get(a, "history&limit=1&failed_only=1")["history"].get("noofslots", 0)
        st = app_get(a, "server_stats")
    except FAIL:
        state["sab"] = {"ok": False}
        return
    num = lambda v: float(v or 0)
    state["sab"] = {
        "ok": True, "version": q.get("version"), "paused": bool(q.get("paused")),
        "speed": num(q.get("kbpersec")) * 1024, "left": num(q.get("mbleft")) * 1048576, "eta": q.get("timeleft"),
        "queueTotal": q.get("noofslots_total", 0),
        "queue": [{"id": s.get("nzo_id"), "title": html.unescape(s.get("filename") or ""), "pct": num(s.get("percentage")), "left": s.get("timeleft"), "size": s.get("size"), "state": s.get("status")} for s in q.get("slots", [])],
        "failed": failed,
        "totals": {k: st.get(k, 0) for k in ("day", "week", "month", "total")},
    }


# ---------- what needs attention ----------
def app_state(key):
    if key in BUILTIN:
        return state["sab" if key == "sabnzbd" else key]
    return state["custom"].get(key, {})


def alerts():
    out = []
    add = lambda level, source, msg: out.append({"level": level, "source": source, "message": msg})
    if not PVE:
        add("warning", "Proxmox", "Not set up yet, so there are no host stats. Open the settings to add it.")
    elif state["proxmox"].get("ok") is False:
        add("error", "Proxmox", f"API not reachable ({state['proxmox'].get('error', 'error')})")
    guests = {str(g["id"]): g for g in state["proxmox"].get("guests", [])}
    guests.update({str(g["name"]): g for g in state["proxmox"].get("guests", []) if g.get("name")})
    if state["proxmox"].get("ok"):
        for want in WATCH_GUESTS:
            g = guests.get(want)
            if not g:
                add("warning", "Proxmox", f"Guest {want} not found on {PVE['node']}")
            elif g["status"] != "running":
                add("error", "Proxmox", f"{g['type'].upper()} {g['id']} ({g.get('name') or '?'}) is {g['status']}")
    for s in state["services"]:
        if not s["up"]:
            add("error", s["name"], "Not reachable")
        elif app_state(s["key"]).get("ok") is False:
            add("error", s["name"], "Reachable, but the API isn't answering: check the URL and API key")
    for a in APPS:
        if a["kind"] in ("sabnzbd",):
            continue
        s = app_state(a["key"])
        if not s.get("ok"):
            continue
        for h in s.get("health", []):
            add("error" if h["level"] == "error" else "warning", a["name"], h["message"])
        for r in s.get("roots", []):
            if not r["ok"]:
                add("error", a["name"], f"Root folder not reachable: {r['path']}")
        if "roots" in s and not s["roots"]:
            add("warning", a["name"], "No root folder set, so nothing can be imported")
    sab = state["sab"]
    if sab.get("ok"):
        if sab["paused"]:
            add("warning", "SABnzbd", "Downloads are paused")
        if sab["failed"]:
            add("warning", "SABnzbd", f"{sab['failed']} failed download(s) in history")
    for d in state["disks"]:
        if d["free"] / d["total"] < 0.1:
            add("warning", "Drives", f"{d['name']} is over 90% full")
    net = state["network"]
    if net and not net["online"]:
        add("error", "Network", "No internet connection")
    if state["system"].get("memory", {}).get("free_level", 100) < 10:
        add("warning", "Memory", "Memory is running low")
    recent = list(history["cpu"])[-30:]
    if len(recent) == 30 and min(recent) > 90:
        add("warning", "CPU", "Above 90% for the last minute")
    return out


def payload():
    upcoming = sorted(state["radarr"].get("upcoming", []) + state["sonarr"].get("upcoming", []), key=lambda u: u["when"])[:8]
    issues = alerts()
    services = [{"name": s["name"], "up": s["up"], "issues": [a for a in issues if a["source"] == s["name"]], "task": activity(s["name"]) if s["up"] else None, "next": up_next(s["name"]) if s["up"] else []} for s in state["services"]]
    sab = state["sab"]
    if sab.get("queue"):  # downloads by the name they'll have in the library
        sab = {**sab, "queue": [{**q, "name": download_titles.get((q.get("id") or "").lower()) or tidy(q["title"])} for q in sab["queue"]]}
    body = {k: v for k, v in state.items() if k != "custom"}
    return json.dumps({
        "title": TITLE, "now": int(time.time() * 1000), "alerts": issues, "history": {k: list(v) for k, v in history.items()},
        "upcoming": upcoming, **body, "services": services, "sab": sab, "home": home_payload(),
    })


# ---------- accounts ----------
failures = {}  # ip -> [attempts, locked until]
ITERATIONS = 300_000


def private_file(path, make):
    """A value in a file only this user can read, created the first time it's needed."""
    try:
        with open(path, encoding="utf-8") as f:
            value = f.read().strip()
        if value:
            return value
    except OSError:
        pass
    value = make()
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        f.write(value + "\n")
    return value


def hash_password(pw, salt=None, iterations=ITERATIONS):
    salt = salt or secrets.token_hex(16)
    return {"salt": salt, "iterations": iterations, "hash": hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), iterations).hex()}


def check_login(user, pw):
    if not SETUP_DONE:
        return False
    given = hash_password(pw, AUTH["salt"], AUTH.get("iterations", ITERATIONS))["hash"]
    return hmac.compare_digest(user.strip().lower().encode(), AUTH["user"].lower().encode()) & hmac.compare_digest(given, AUTH["hash"])


def session_token():
    # Tied to the password hash: changing the password signs everyone out.
    return hmac.new(private_file(SECRET_FILE, lambda: secrets.token_hex(32)).encode(), (AUTH.get("user", "") + AUTH.get("hash", "")).encode(), hashlib.sha256).hexdigest()


def setup_token():
    return private_file(SETUP_TOKEN_FILE, lambda: secrets.token_hex(4) + "-" + secrets.token_hex(4))


# ---------- the setup wizard ----------
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")
CUSTOM_KINDS = ("arr", "generic")
AUTH_KINDS = ("header", "query", "none")


def public_config():
    """The settings for the wizard to show: everything except secrets, which are only flagged as set."""
    c = CONFIG
    p = c.get("proxmox") or {}
    return {
        "setup_done": SETUP_DONE, "demo": DEMO, "title": TITLE, "user": AUTH.get("user", ""),
        "domain": c.get("domain", ""), "tls": c.get("tls", "public"), "ip_access": c.get("ip_access", True),
        "caddy": bool(c.get("caddy_site_file")), "lan": state["network"].get("lan", ""), "port": PORT,
        "proxmox": {"url": p.get("url", ""), "node": p.get("node", ""), "token_id": p.get("token_id", ""), "has_secret": bool(p.get("token_secret")),
                    "verify_tls": p.get("verify_tls", True), "ca_file": p.get("ca_file", "")},
        "storages": STORAGES, "watch_guests": WATCH_GUESTS,
        "apps": {k: {"enabled": bool(a.get("url")) and a.get("enabled", True), "url": a.get("url", ""), "has_key": bool(a.get("api_key")), "verify_tls": a.get("verify_tls", True)}
                 for k, a in ((k, (c.get("apps") or {}).get(k) or {}) for k in BUILTIN)},
        "builtin": {k: {"name": b["name"], "port": b["port"]} for k, b in BUILTIN.items()},
        "custom_apps": [{**{k: v for k, v in a.items() if k != "api_key"}, "has_key": bool(a.get("api_key"))} for a in c.get("custom_apps") or []],
        "home": {"enabled": bool(HOME.get("enabled")), "side": HOME.get("side", "right"), "layout": HOME.get("layout", "grid"),
                 "start": bool(HOME.get("start")), "tiles": HOME.get("tiles") or []},
    }


def wizard_test(req):
    """Try a connection with what the wizard has filled in so far. Returns what was found, or why it failed."""
    what = req.get("what")
    try:
        if what == "proxmox":
            p = dict(req.get("proxmox") or {})
            if not p.get("token_secret"):
                p["token_secret"] = (CONFIG.get("proxmox") or {}).get("token_secret", "")
            for k in ("url", "token_id", "token_secret"):
                if not p.get(k):
                    return {"ok": False, "error": f"{k} fehlt"}
            nodes = [n["node"] for n in pve_get(p, "nodes")]
            node = p.get("node") if p.get("node") in nodes else nodes[0]
            storages = [{"name": s["storage"], "type": s.get("type"), "total": s.get("total", 0), "used": s.get("used", 0)}
                        for s in pve_get(p, f"nodes/{node}/storage") if s.get("active") and s.get("total")]
            guests = [{"id": g["vmid"], "name": g.get("name"), "type": g.get("type"), "status": g.get("status")}
                      for g in pve_get(p, "cluster/resources?type=vm") if g.get("node") == node]
            version = pve_get(p, "version").get("version")
            return {"ok": True, "version": version, "nodes": nodes, "node": node, "storages": storages, "guests": sorted(guests, key=lambda g: g["id"])}
        if what == "app":
            a = dict(req.get("app") or {})
            key = a.get("key", "")
            saved = ((CONFIG.get("apps") or {}).get(key) if key in BUILTIN else
                     next((c for c in CONFIG.get("custom_apps") or [] if c.get("name") == a.get("name")), None)) or {}
            if not a.get("api_key"):
                a["api_key"] = saved.get("api_key", "")
            if not a.get("url"):
                return {"ok": False, "error": "URL fehlt"}
            app = make_app(key or "custom", a, BUILTIN.get(key))
            if app["kind"] == "sabnzbd":
                v = app_get(app, "version").get("version")
                app_get(app, "queue&limit=1")["queue"]  # version works without a key; the queue proves the key
                return {"ok": True, "version": v}
            if app["kind"] == "bazarr":
                return {"ok": True, "version": (app_get(app, "system/status").get("data") or {}).get("bazarr_version")}
            if app["kind"] in ("radarr", "sonarr", "prowlarr", "arr"):
                st = app_get(app, "system/status")
                return {"ok": True, "version": st.get("version"), "app": st.get("appName")}
            app_get(app, app["health_path"], raw=True)
            return {"ok": True}
        return {"ok": False, "error": "unbekannter Test"}
    except urllib.error.HTTPError as e:
        hint = {401: "API-Key/Token falsch", 403: "keine Berechtigung", 404: "Pfad nicht gefunden (URL-Basis prüfen)"}.get(e.code, "")
        return {"ok": False, "error": f"HTTP {e.code} {hint}".strip()}
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            return {"ok": False, "error": f"Zertifikat nicht vertrauenswürdig ({reason.verify_message}). CA-Datei angeben oder Prüfung abschalten."}
        return {"ok": False, "error": f"nicht erreichbar: {reason}"}
    except FAIL as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}


def clean_url(u):
    u = (u or "").strip().rstrip("/")
    p = urllib.parse.urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError(f"Ungültige URL: {u or '(leer)'}")
    return u


def build_config(req):
    """The new config.json from the wizard's answers, keeping saved secrets that weren't re-entered."""
    old = CONFIG
    c = {k: v for k, v in old.items() if k in ("port", "bind", "trusted_proxies", "caddy_site_file", "internet_check")}
    c["title"] = (req.get("title") or "").strip()[:80] or "Media Server Dashboard"

    user = (req.get("user") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._@-]{2,64}", user):
        raise ValueError("Benutzername: 2–64 Zeichen, nur Buchstaben, Ziffern und . _ @ -")
    pw = req.get("password") or ""
    if pw:
        if len(pw) < 10:
            raise ValueError("Passwort: mindestens 10 Zeichen")
        c["auth"] = {"user": user, **hash_password(pw)}
    elif old.get("auth", {}).get("hash"):
        c["auth"] = {**old["auth"], "user": user}
    else:
        raise ValueError("Bitte ein Passwort setzen")

    domain = (req.get("domain") or "").strip().lower().rstrip(".")
    if domain and not DOMAIN_RE.match(domain):
        raise ValueError(f"Ungültige Domain: {domain}")
    c["domain"] = domain
    c["tls"] = "internal" if req.get("tls") == "internal" else "public"
    c["ip_access"] = bool(req.get("ip_access", True))

    p = req.get("proxmox") or {}
    if p.get("url"):
        secret = p.get("token_secret") or (old.get("proxmox") or {}).get("token_secret", "")
        if not (p.get("node") and p.get("token_id") and secret):
            raise ValueError("Proxmox: Node, Token-ID und Secret werden gebraucht")
        c["proxmox"] = {"url": clean_url(p["url"]), "node": p["node"].strip(), "token_id": p["token_id"].strip(), "token_secret": secret,
                        "verify_tls": bool(p.get("verify_tls", True)), **({"ca_file": p["ca_file"].strip()} if (p.get("ca_file") or "").strip() else {})}
    c["storages"] = [str(s) for s in req.get("storages") or []]
    c["watch_guests"] = [str(g) for g in req.get("watch_guests") or []]

    c["apps"] = {}
    for key in BUILTIN:
        a = (req.get("apps") or {}).get(key) or {}
        if not a.get("enabled"):
            continue
        k = a.get("api_key") or ((old.get("apps") or {}).get(key) or {}).get("api_key", "")
        if not k:
            raise ValueError(f"{BUILTIN[key]['name']}: API-Key fehlt")
        c["apps"][key] = {"url": clean_url(a.get("url")), "api_key": k, "verify_tls": bool(a.get("verify_tls", True))}

    c["custom_apps"] = []
    seen = set(b["name"].lower() for b in BUILTIN.values())
    for a in req.get("custom_apps") or []:
        name = (a.get("name") or "").strip()[:40]
        if not name:
            raise ValueError("Eigene App ohne Namen")
        if name.lower() in seen:
            raise ValueError(f"Name doppelt: {name}")
        seen.add(name.lower())
        kind = a.get("kind") if a.get("kind") in CUSTOM_KINDS else "generic"
        auth = a.get("auth") if a.get("auth") in AUTH_KINDS else "header"
        prev = next((x for x in old.get("custom_apps") or [] if x.get("name") == name), {})
        entry = {"name": name, "kind": kind, "url": clean_url(a.get("url")), "auth": auth, "verify_tls": bool(a.get("verify_tls", True)),
                 "api_key": a.get("api_key") or prev.get("api_key", "")}
        if auth != "none":
            entry["auth_name"] = (a.get("auth_name") or "X-Api-Key").strip()[:64]
        if kind == "arr":
            entry["api_version"] = a.get("api_version") if a.get("api_version") in ("v1", "v3") else "v1"
        else:
            entry["health_path"] = "/" + (a.get("health_path") or "/").strip().lstrip("/")
        if kind == "arr" and not entry["api_key"]:
            raise ValueError(f"{name}: API-Key fehlt")
        c["custom_apps"].append(entry)

    h = req.get("home") or {}
    tiles = []
    for t in (h.get("tiles") or [])[:60]:
        name = (t.get("name") or "").strip()[:40]
        if not name:
            raise ValueError("Kachel ohne Namen")
        url = (t.get("url") or "").strip()
        check = (t.get("check") or "").strip()
        if url:
            url = clean_url(url)
        if check and not re.fullmatch(r"\[?[A-Za-z0-9.:-]+\]?(:\d{1,5})?", check):
            raise ValueError(f"{name}: IP:Port ungültig")
        if not url and not check:
            raise ValueError(f"{name}: URL oder IP:Port angeben")
        tiles.append({"name": name, "url": url, "check": check, "guest": str(t.get("guest") or ""), "icon": clean_icon(t.get("icon"), name)})
    c["home"] = {"enabled": bool(h.get("enabled")), "side": "left" if h.get("side") == "left" else "right",
                 "layout": "table" if h.get("layout") == "table" else "grid", "start": bool(h.get("start")), "tiles": tiles}
    return c


ICON_RE = re.compile(r"^data:image/(png|jpeg|webp|gif|svg\+xml);base64,[A-Za-z0-9+/=]+$")


def clean_icon(icon, name):
    """An icon is either a few characters (an emoji) or a small image the wizard has already shrunk."""
    icon = (icon or "").strip()
    if icon.startswith("data:"):
        if len(icon) > 150_000 or not ICON_RE.match(icon):
            raise ValueError(f"{name}: Icon zu groß oder kein Bild")
        return icon
    return icon[:8]


def caddy_site(c):
    """The Caddy site for the reverse proxy in the LXC: HTTPS on the domain if there is one, plain HTTP by IP otherwise."""
    up = f"reverse_proxy 127.0.0.1:{c.get('port') or 8900}"
    blocks = []
    if c.get("domain"):
        tls = "\n    tls internal" if c.get("tls") == "internal" else ""
        blocks.append(f"{c['domain']} {{\n    {up}{tls}\n}}")
    if not c.get("domain") or c.get("ip_access", True):
        blocks.append(f"http:// {{\n    {up}\n}}")
    return "# Written by the dashboard's setup wizard; changes here are overwritten.\n" + "\n\n".join(blocks) + "\n"


def write_private(path, text):
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def save_and_restart(c):
    write_private(CONFIG_FILE, json.dumps(c, indent=2) + "\n")
    if c.get("caddy_site_file"):
        with open(c["caddy_site_file"], "w", encoding="utf-8") as f:  # a systemd path unit reloads Caddy when this changes
            f.write(caddy_site(c))
    try:
        os.remove(SETUP_TOKEN_FILE)
    except OSError:
        pass

    def restart():
        time.sleep(0.7)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=restart, daemon=True).start()


def dashboard_url(c, lan):
    if c.get("domain") and c.get("caddy_site_file"):
        return f"https://{c['domain']}/"
    if c.get("caddy_site_file"):
        return f"http://{lan}/" if lan else "/"
    return f"http://{c['domain'] or lan or 'localhost'}:{c.get('port') or 8900}/"


# ---------- web server ----------
class Handler(BaseHTTPRequestHandler):
    def client_ip(self):
        ip = self.client_address[0]
        if ip in TRUSTED_PROXIES:
            forwarded = [p.strip() for p in self.headers.get("X-Forwarded-For", "").split(",") if p.strip()]
            if forwarded:
                return forwarded[-1]  # the address the proxy itself saw; earlier entries are client-supplied
        return ip

    def https(self):
        return self.client_address[0] in TRUSTED_PROXIES and self.headers.get("X-Forwarded-Proto", "") == "https"

    def authed(self):
        if DEMO and not SETUP_DONE:
            return True
        cookies = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return SETUP_DONE and "dash_auth" in cookies and hmac.compare_digest(cookies["dash_auth"].value, session_token())

    def may_setup(self):
        """The wizard is open to a signed-in user, or before the first setup to whoever has the setup token."""
        if self.authed():
            return True
        if SETUP_DONE:
            return False
        return hmac.compare_digest(self.headers.get("X-Setup-Token", "").strip().encode(), setup_token().encode())

    def redirect(self, to, cookie=None):
        self.send_response(303)
        self.send_header("Location", to)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def page(self, name):
        with open(os.path.join(HERE, name), "rb") as f:
            self.reply(200, f.read().replace(b"{{TITLE}}", html.escape(TITLE).encode()), "text/html; charset=utf-8")

    def json(self, code, obj):
        self.reply(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/healthz":
            return self.reply(200, b"ok", "text/plain")
        if path == "/setup":
            return self.page("setup.html") if self.authed() or not SETUP_DONE else self.redirect("/login")
        if path == "/setup/config":
            return self.json(200, public_config()) if self.may_setup() else self.json(401, {"error": "Setup-Token oder Login nötig"})
        if not SETUP_DONE and not DEMO:
            return self.redirect("/setup")
        if path == "/login":
            return self.page("login.html")
        if path == "/logout":
            return self.redirect("/login", "dash_auth=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        if path == "/api/data":
            if not self.authed():
                return self.reply(401, b"Login needed", "text/plain")
            return self.reply(200, (demo.payload(TITLE) if DEMO else payload()).encode(), "application/json")
        if DEMO and path.startswith("/demo-poster/"):
            return self.reply(200, demo.poster(path), "image/svg+xml")
        if path in ("/", "/office"):
            return self.page("index.html" if path == "/" else "office.html") if self.authed() else self.redirect("/login")
        if path == "/poster":
            return self.poster() if self.authed() else self.reply(401, b"Login needed", "text/plain")
        if path in VENDOR:
            if not self.authed():
                return self.reply(401, b"Login needed", "text/plain")
            with open(os.path.join(HERE, "vendor", VENDOR[path]), "rb") as f:
                return self.reply(200, f.read(), "text/javascript; charset=utf-8")
        self.reply(404, b"Not found", "text/plain")

    def poster(self):
        """Posters for the office board, fetched here so the 3D page can draw them (the image sites don't allow that directly)."""
        url = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("u", [""])[0]
        if DEMO and url.startswith("/demo-poster/"):
            return self.reply(200, demo.poster(url), "image/svg+xml")
        if not url.startswith(POSTER_HOSTS):
            return self.reply(400, b"Not a poster", "text/plain")
        url = url.replace("/t/p/original/", "/t/p/w500/")
        with poster_lock:
            cached = poster_cache.get(url)
        if not cached:
            try:
                with urllib.request.urlopen(url, timeout=8, context=DEFAULT_SSL) as r:
                    cached = (r.read(2_000_000), r.headers.get("Content-Type", "image/jpeg"))
            except OSError:
                return self.reply(502, b"Poster unavailable", "text/plain")
            with poster_lock:
                poster_cache[url] = cached
                while len(poster_cache) > 40:
                    poster_cache.pop(next(iter(poster_cache)))
        self.reply(200, *cached)

    def body(self, limit=65536):
        size = min(int(self.headers.get("Content-Length") or 0), limit)
        return self.rfile.read(size)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/login":
            return self.login()
        if path in ("/setup/test", "/setup/save"):
            # JSON only, and from this page: a form on another site can't send either.
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self.json(415, {"error": "JSON erwartet"})
            if not self.may_setup():
                return self.json(401, {"ok": False, "error": "Setup-Token falsch oder nicht angemeldet"})
            try:
                req = json.loads(self.body() or b"{}")
            except ValueError:
                return self.json(400, {"ok": False, "error": "Ungültiges JSON"})
            if path == "/setup/test":
                return self.json(200, wizard_test(req))
            if DEMO:
                return self.json(400, {"ok": False, "error": "Im Demo-Modus wird nichts gespeichert"})
            try:
                c = build_config(req)
            except ValueError as e:
                return self.json(400, {"ok": False, "error": str(e)})
            self.json(200, {"ok": True, "url": dashboard_url(c, state["network"].get("lan", ""))})
            return save_and_restart(c)
        self.reply(404, b"Not found", "text/plain")

    def login(self):
        ip = self.client_ip()
        fail = failures.get(ip, [0, 0])
        if fail[1] > time.time():
            return self.redirect("/login?e=locked")
        form = urllib.parse.parse_qs(self.body(4096).decode("utf-8", "replace"))
        user, given = form.get("user", [""])[0], form.get("password", [""])[0]
        if check_login(user, given):
            failures.pop(ip, None)
            secure = "; Secure" if self.https() else ""
            return self.redirect("/office" if HOME.get("enabled") and HOME.get("start") else "/", f"dash_auth={session_token()}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict{secure}")
        attempts = 1 if fail[1] else fail[0] + 1  # a finished lockout starts counting again
        failures[ip] = [attempts, time.time() + LOCKOUT if attempts >= MAX_ATTEMPTS else 0]
        time.sleep(0.8)
        self.redirect("/login?e=1")

    def reply(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    print(f"{TITLE}{' (demo)' if DEMO else ''}: listening on {BIND}:{PORT}", flush=True)
    if not SETUP_DONE:
        print(f"Setup not finished: open /setup and enter the setup token: {setup_token()}  (also in {SETUP_TOKEN_FILE})", flush=True)
    if DEMO:
        import demo
    else:
        looped(30, read_network)
        if HOME.get("enabled") and HOME.get("tiles"):
            looped(10, read_home)
        if PVE:
            threading.Thread(target=system_loop, daemon=True).start()
            looped(30, read_disks)
        if APPS:
            print("watching: " + ", ".join(a["name"] for a in APPS), flush=True)
            looped(5, read_services)
            looped(5, read_sab)
            looped(5, read_radarr)
            looped(5, read_sonarr)
            looped(60, read_prowlarr)
            looped(30, read_bazarr)
            looped(15, read_custom)
            looped(5, read_activity)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
