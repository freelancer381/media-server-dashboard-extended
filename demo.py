"""Made-up data for `python3 server.py --demo`: the same shape as the real thing, nothing read from anywhere.

Everything moves with the clock, so the graphs scroll and the workers come and go like they would for real.
"""
import html
import json
import os
import math
import time

GB = 1024 ** 3
MOVIES = [
    ("The Last Lighthouse", 2024, "#1d3557", "#e9c46a"), ("Neon Harbor", 2023, "#3a0ca3", "#f72585"),
    ("Paper Moons", 2025, "#264653", "#2a9d8f"), ("Copper Sky", 2022, "#6a040f", "#f48c06"),
    ("Midnight Orchard", 2024, "#081c15", "#74c69d"), ("Glass Mountain", 2021, "#22223b", "#9a8c98"),
]
SHOWS = [
    ("Harbor Lights", "S02E05", "#003049", "#fcbf49"), ("The Quiet Office", "S01E08", "#2b2d42", "#8d99ae"),
    ("Starfield Kitchen", "S03E02", "#5f0f40", "#fb8b24"), ("North Line", "S01E03", "#012a4a", "#61a5c2"),
]
PEOPLE = ["Alex", "Sam", "Jordan"]


def wave(t, period, lo, hi, seed=0):
    """A smooth but uneven value between lo and hi."""
    v = (math.sin(t / period + seed) + 0.5 * math.sin(t / (period * 0.37) + seed * 2) + 0.25 * math.sin(t / (period * 0.11) + seed * 3)) / 1.75
    return lo + (hi - lo) * (v + 1) / 2


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))


def payload(title, version="demo", boot=""):
    now = time.time()
    ms = int(now * 1000)
    ts = [now - (149 - i) * 2 for i in range(150)]
    cpu = [round(wave(t, 40, 8, 78, 1)) for t in ts]
    mem = [round(wave(t, 300, 61, 72, 2), 1) for t in ts]
    rx = [round(wave(t, 25, 0.5, 11, 3) * 1048576) for t in ts]
    tx = [round(wave(t, 30, 0.05, 1.2, 4) * 1048576) for t in ts]
    disk = [wave(t, 15, 0, 40, 5) * 1048576 for t in ts]
    sab = [r * 0.9 for r in rx]

    phase = int(now // 45) % 4  # every 45 seconds someone else is busy
    # For 45 seconds every 5 minutes the Proxmox host "goes away", so the office's panic can be seen.
    fire = os.environ.get("MSD_DEMO_FIRE") == "1" or int(now) % 300 >= 255
    queue = [
        {"id": f"demo{i}", "title": f"{n}.{y}.1080p.WEB-DL", "name": f"{n} ({y})", "pct": max(0.0, round(wave(now, 20, 20, 95, i) - i * 25, 1)),
         "left": f"0:{12 + i * 7:02d}:00", "size": f"{3 + i}.{i} GB", "state": "Downloading" if i == 0 else "Queued"}
        for i, (n, y, *_ ) in enumerate(MOVIES[:5])
    ]
    # qBittorrent next to SABnzbd: two download clients, shown together on the download card
    torrents = [{"id": f"qb{i}", "title": f"{n}.{e}.1080p.WEB", "name": f"{n} {e}", "pct": max(0.0, round(wave(now, 30, 10, 90, i + 7) - i * 30, 1)),
                 "left": f"0:{20 + i * 9:02d}:00", "size": f"{1 + i}.{i + 2} GB", "state": "Downloading" if i == 0 else "Queued", "client": "qBittorrent"}
                for i, (n, e, *_) in enumerate(SHOWS[:3])]
    for q in queue:
        q["client"] = "SABnzbd"
    qb_speed = wave(now, 35, 0.3, 4, 9) * 1048576
    watching = PEOPLE[phase % len(PEOPLE)] if phase != 3 else None
    streams = [{"title": f"{MOVIES[phase][0]} ({MOVIES[phase][1]})", "who": watching, "pct": round(wave(now, 200, 10, 90))}] if watching else []
    watched = [{"who": PEOPLE[i % 3], "title": f"{SHOWS[i % 4][0]} {SHOWS[i % 4][1]}", "year": None, "pct": 100 - i * 13,
                "when": ms - (i + 1) * 3_600_000, "now": False} for i in range(5)]

    activity = {
        "Radarr": "Waiting on 3 downloads" if phase in (0, 1) else None,
        "Sonarr": "RefreshMonitoredDownloads" if phase in (1, 2) else None,
        "Prowlarr": "Searching for Paper Moons" if phase == 2 else None,
        "Bazarr": "Search for missing subtitles" if phase == 3 else None,
    }
    names = ["Home Cinema", "Radarr", "Sonarr", "Bazarr", "Prowlarr", "SABnzbd", "qBittorrent", "Lidarr", "Jellyfin", "Readarr"]
    # Clicking a worker in the office opens its app; in the demo, the project's website.
    sites = {"Radarr": "https://radarr.video", "Sonarr": "https://sonarr.tv", "Bazarr": "https://www.bazarr.media", "Prowlarr": "https://prowlarr.com",
             "SABnzbd": "https://sabnzbd.org", "qBittorrent": "https://www.qbittorrent.org", "Lidarr": "https://lidarr.audio", "Jellyfin": "https://jellyfin.org", "Readarr": "https://readarr.com"}
    tasks = {**activity, "SABnzbd": f"Downloading {queue[0]['name']} · {round(queue[0]['pct'])}%",
             "qBittorrent": f"Downloading {torrents[0]['name']} · {round(torrents[0]['pct'])}%" if phase != 2 else None,
             "Home Cinema": f"Streaming {streams[0]['title']} to {watching}" if streams else None}
    nxt = [{"title": q["name"], "pct": round(q["pct"]), "size": q["size"]} for q in queue]
    tasks.update({"Lidarr": "Searching for new albums" if phase in (0, 2) else None, "Jellyfin": "Transcoding a stream" if phase == 1 else None,
                  "Readarr": "RefreshAuthor" if phase == 3 else None})
    nxt_qb = [{"title": q["name"], "pct": round(q["pct"]), "size": q["size"]} for q in torrents]
    services = [{"name": n, "up": True, "url": sites.get(n, ""), "issues": [], "task": tasks.get(n),
                 "next": nxt if n in ("SABnzbd", "Radarr") else nxt_qb if n in ("qBittorrent", "Sonarr") else []} for n in names]

    poster = lambda kind, i: f"/demo-poster/{kind}{i}.svg"
    return json.dumps({
        "title": title, "now": ms, "alerts": [{"level": "error", "source": "Proxmox", "message": "API not reachable (URLError: timed out)"}] if fire else [],
        "history": {"t": [int(t * 1000) for t in ts], "cpu": cpu, "mem": mem, "rx": rx, "tx": tx, "disk": disk, "sab": sab},
        "upcoming": [{"kind": "Episode", "title": f"{SHOWS[i][0]} S0{i + 1}E0{i + 4}", "when": iso(ms + (i + 1) * 86_400_000)} for i in range(3)]
        + [{"kind": "Movie", "title": MOVIES[5][0], "when": iso(ms + 5 * 86_400_000)}],
        "system": {"host": "pve-demo", "cpu_name": "AMD Ryzen 7 5700G", "cores": 16, "pve": "pve-manager/8.3.0", "booted": ms - 3 * 86_400_000 - 7_200_000,
                   "speed_limit": 100, "cpu": {"user": cpu[-1] * 0.7, "system": cpu[-1] * 0.3, "idle": 100 - cpu[-1]}, "load": [2.1, 1.8, 1.6],
                   "memory": {"total": 64 * GB, "used": int(mem[-1] / 100 * 64 * GB), "free_level": 40, "swap_total": 0, "swap_used": 0}},
        "services": services,
        "cinema": {"enabled": True, "ok": True, "server": "Jellyfin", "transcoding": 1 if streams else 0, "converting": 0, "profiles": PEOPLE, "streams": streams, "watched": watched, "missing_drives": []},
        "sab": {"ok": True, "client": "SABnzbd + qBittorrent", "version": None, "paused": False, "speed": sab[-1] + qb_speed, "left": 27 * GB, "eta": "0:41:10",
                "queueTotal": 15, "queue": [q for pair in zip(queue, torrents + [None] * len(queue)) for q in pair if q], "failed": 0,
                "totals": {"day": 45 * GB, "week": 251 * GB, "month": 990 * GB, "total": 11900 * GB}},
        "radarr": {"ok": True, "version": "5.0", "library": {"total": 412, "have": 398, "size": 3900 * GB}, "missing": 14,
                   "recent": [{"title": n, "year": y, "poster": poster("m", i), "size": 8 * GB, "quality": "Bluray-1080p", "when": iso(ms - (i + 1) * 5_400_000),
                              "open": "https://www.themoviedb.org/search?query=" + n.replace(" ", "+")} for i, (n, y, *_) in enumerate(MOVIES)],
                   "upcoming": [], "health": [], "roots": [{"path": "/data/media/movies", "ok": True}]},
        "sonarr": {"ok": True, "version": "4.0", "library": {"total": 64, "have": 2210, "episodes": 2380, "size": 5100 * GB}, "missing": 30,
                   "recent": [{"title": n, "sub": e, "poster": poster("s", i), "quality": "WEBDL-1080p", "when": iso(ms - (i + 1) * 7_200_000)} for i, (n, e, *_) in enumerate(SHOWS)],
                   "upcoming": [], "health": [], "roots": [{"path": "/data/media/tv", "ok": True}]},
        "prowlarr": {"ok": True, "version": "1.0", "indexers": 6, "enabled": 6, "health": []},
        "bazarr": {"ok": True, "health": []},
        "activity": activity,
        "network": {"lan": "", "vpn": False, "online": True, "ping": 14.0},
        "disks": [{"name": "local-zfs", "total": 900 * GB, "free": 610 * GB}, {"name": "media", "total": 16000 * GB, "free": 2700 * GB}],
        "proxmox": {"ok": not fire, "guests": [], **({"error": "URLError: timed out"} if fire else {})},
        "version": version, "boot": boot, "update": {"available": None, "state": "idle", "last": {}, "acked": True},
        "home": {"enabled": True, "side": "right", "layout": "grid", "tiles": [
            {"name": n, "url": "", "icon": ic, "check": f"192.168.1.{20 + i}:{port}", "up": True, "ms": 1 + i % 3,
             "guest": {"id": 101 + i, "name": n.lower(), "type": "lxc", "status": "running", "cpu": round(wave(now, 30 + i * 7, 1, 35, i), 1),
                       "mem": int(wave(now, 90, 0.3, 1.6, i) * GB), "maxmem": 2 * GB, "rx": int(rx[-1] * (0.6 if n == "SABnzbd" else 0.05)),
                       "tx": int(tx[-1] * 0.2), "uptime": 86_400 * (3 + i)}}
            for i, (n, ic, port) in enumerate([("Radarr", "🎬", 7878), ("Sonarr", "📺", 8989), ("SABnzbd", "📥", 8080), ("Jellyfin", "🍿", 8096)])
        ] + [{"name": "Home Assistant", "url": "", "icon": "🏠", "check": "192.168.1.40:8123", "up": True, "ms": 2, "guest": None},
             {"name": "NAS", "url": "", "icon": "🗄️", "check": "192.168.1.5:5000", "up": False, "ms": None, "guest": None}]},
    })


def events(live):
    """Made-up chat lines for the office, every few seconds, like a busy evening."""
    import random
    script = [
        ("Prowlarr", "search", "searching: {m}", "info"),
        ("Radarr", "grab", "grabbed {m} in Bluray-1080p", "info"),
        ("SABnzbd", "download", "finished downloading {m}", "success"),
        ("Radarr", "import", "imported {m}", "success"),
        ("Bazarr", "subtitle", "found English subtitles for {m}", "success"),
        ("Sonarr", "grab", "grabbed {s}", "info"),
        ("Sonarr", "import", "imported {s}", "success"),
        ("Home Cinema", "play", "{p} is watching {m}", "info"),
        ("Radarr", "upgrade", "upgraded {m} to Bluray-2160p", "success"),
    ]
    time.sleep(3)
    if not live.recent(1):
        live.emit("Radarr", "test", "Demo mode: what your apps are doing shows up here 👋", "info")
    while True:
        source, kind, text, level = random.choice(script)
        m = "%s (%d)" % random.choice(MOVIES)[:2]
        s = "%s %s" % random.choice(SHOWS)[:2]
        live.emit(source, kind, text.format(m=m, s=s, p=random.choice(PEOPLE)), level)
        time.sleep(random.uniform(5, 11))


def poster(path):
    """A simple made-up poster: a gradient with the title on it."""
    key = path.rsplit("/", 1)[-1].removesuffix(".svg")
    items = MOVIES if key[:1] == "m" else SHOWS
    try:
        name, _, top, bottom = items[int(key[1:]) % len(items)]
    except ValueError:
        name, top, bottom = "Demo", "#333", "#777"
    words, lines = name.upper().split(), []
    for w in words:
        if lines and len(lines[-1]) + len(w) < 11:
            lines[-1] += " " + w
        else:
            lines.append(w)
    text = "".join(f'<text x="250" y="{560 + i * 70}" font-size="62" text-anchor="middle" fill="#fff" font-family="Helvetica, Arial, sans-serif" font-weight="700">{html.escape(t)}</text>' for i, t in enumerate(lines))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="500" height="750" viewBox="0 0 500 750">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/></linearGradient></defs>
<rect width="500" height="750" fill="url(#g)"/><circle cx="250" cy="280" r="130" fill="#fff" opacity=".18"/><circle cx="250" cy="280" r="70" fill="#fff" opacity=".22"/>{text}</svg>'''.encode()


def stats(rng, ranges):
    """Made-up long-term numbers for the statistics page, in the shape of server.stats_payload."""
    import random
    from datetime import datetime, timedelta
    tier, span = ranges.get(rng, ranges["24h"])
    step = {"minute": 60, "hour": 3600, "day": 86400}[tier]
    now = time.time()
    t = [int(x // step * step) for x in range(int(now - span), int(now), step)][-2200:]
    daily = lambda x: math.sin((x % 86400) / 86400 * 2 * math.pi - 1.9)  # busy in the evening, quiet at night
    pt = lambda avg, peak: [round(avg, 2), round(max(avg, peak), 2)]
    s = {
        "cpu": [pt(max(1.0, wave(x, 5000, 8, 45, 1) + 12 * daily(x)), wave(x, 3000, 40, 95, 2)) for x in t],
        "mem": [pt(wave(x, 40000, 58, 72, 2), wave(x, 40000, 60, 78, 3)) for x in t],
        "load": [pt(wave(x, 6000, 0.6, 3.5, 4), wave(x, 6000, 1.5, 6, 5)) for x in t],
        "rx": [pt(max(0.0, wave(x, 4000, 0.2, 9, 3) + 4 * daily(x)) * 1048576, wave(x, 2000, 8, 22, 6) * 1048576) for x in t],
        "tx": [pt(wave(x, 4000, 0.05, 1.5, 4) * 1048576, wave(x, 2000, 1, 6, 7) * 1048576) for x in t],
        "disk": [pt(wave(x, 3000, 1, 40, 5) * 1048576, wave(x, 2000, 30, 180, 8) * 1048576) for x in t],
        "guests": [pt(9, 9) if (x // 86400) % 9 else pt(8.6, 9) for x in t],
        "queue": [pt(max(0.0, wave(x, 7000, -4, 14, 6)), max(0.0, wave(x, 7000, -2, 18, 7))) for x in t],
        "alerts": [pt(1, 1) if (x // 3600) % 37 == 0 else pt(0, 0) for x in t],
        "viewers": [pt(max(0.0, 1 + daily(x)), 2 + max(0.0, daily(x)) * 2) for x in t],
        "streams": [pt(max(0.0, 1.2 * daily(x) + 0.6), max(0, round(2.5 * daily(x) + 1.5))) for x in t],
        "transcodes": [pt(max(0.0, 0.5 * daily(x)), max(0, round(daily(x) + 0.3))) for x in t],
    }
    for name, seed, hi in (("SABnzbd", 1, 9), ("qBittorrent", 2, 4)):
        s["dl:" + name] = [pt(max(0.0, wave(x, 3000 + seed * 900, -2, hi, seed) + 2 * daily(x)) * 1048576, wave(x, 2500, hi, hi * 2.2, seed) * 1048576) for x in t]
    s["dl"] = [[round(a[0] + b[0], 2), round(a[1] + b[1], 2)] for a, b in zip(s["dl:SABnzbd"], s["dl:qBittorrent"])]
    for i, name in enumerate(("Radarr", "Sonarr", "Prowlarr", "Bazarr", "SABnzbd", "qBittorrent", "Jellyfin")):
        s["ms:" + name] = [pt(wave(x, 1500 + i * 300, 0.4, 2.5 + i * 0.4, i), wave(x, 900, 3, 12 + i * 2, i)) for x in t]

    rnd = random.Random(42)
    today = datetime.fromtimestamp(now)
    days = []
    n_days = 30 if span <= 30 * 86400 else min(365, int(span // 86400))
    for i in range(n_days - 1, -1, -1):
        d = today - timedelta(days=i)
        weekend = d.weekday() >= 5
        k = {"grab": rnd.randint(4, 18), "download": rnd.randint(3, 16), "import": rnd.randint(3, 15), "upgrade": rnd.randint(0, 4),
             "subtitle": rnd.randint(1, 9), "search": rnd.randint(5, 30), "play": rnd.randint(1, 6) + (5 if weekend else 0),
             "health": rnd.choice([0, 0, 0, 1, 2]), "error": rnd.choice([0, 0, 0, 0, 1]), "recover": rnd.choice([0, 0, 1])}
        hours = [max(0, int(rnd.gauss(2 + 5 * max(0, math.sin((h - 7) / 24 * 2 * math.pi)), 1.2))) for h in range(24)]
        days.append({"date": d.strftime("%Y-%m-%d"), "kinds": k, "hours": hours, "xp": sum(k.values()) * 9,
                     "dl": {"sabnzbd": int(rnd.uniform(15, 60) * GB), "qbittorrent": int(rnd.uniform(3, 25) * GB)},
                     "sources": {"Radarr": k["grab"] // 2 + k["import"], "Sonarr": k["grab"] - k["grab"] // 2 + k["upgrade"], "SABnzbd": k["download"] - 2,
                                 "qBittorrent": 2, "Prowlarr": k["search"], "Bazarr": k["subtitle"], "Home Cinema": k["play"], "Proxmox": k["health"]},
                     "via": {"hook": int(sum(k.values()) * 0.7), "poll": int(sum(k.values()) * 0.3)},
                     "levels": {"success": k["download"] + k["import"] + k["subtitle"], "info": k["grab"] + k["search"] + k["play"], "warning": k["health"], "error": k["error"]}})

    p = json.loads(payload("demo"))
    guests = [{"id": 100 + i, "name": n, "type": "lxc" if i else "vm", "status": "running" if i != 6 else "stopped", "cpu": round(wave(now, 40 + i * 9, 0.5, 30, i), 1),
               "mem": int(wave(now, 100, 0.2, 3.5, i) * GB), "maxmem": 4 * GB, "uptime": 86400 * (2 + i * 3), "rx": int(wave(now, 20, 0, 6, i) * 1048576), "tx": int(wave(now, 25, 0, 0.8, i) * 1048576)}
              for i, n in enumerate(["home-assistant", "radarr", "sonarr", "prowlarr", "sabnzbd", "jellyfin", "windows-vm", "dashboard", "qbittorrent"])]
    svc = [{"name": x["name"], "up": x["up"], "ms": round(wave(now, 50, 0.4, 3.2, i), 1)} for i, x in enumerate(p["services"])]
    movie = {"version": "5.14.0", "library": p["radarr"]["library"], "missing": 14, "nerd": {
        "quality": [["Bluray-1080p", 182], ["WEBDL-1080p", 96], ["Bluray-2160p", 61], ["WEBRip-1080p", 28], ["Remux-2160p", 19], ["HDTV-720p", 12]],
        "resolution": [["1080p", 306], ["2160p", 80], ["720p", 12]], "video": [["x265", 214], ["h264", 166], ["AV1", 18]],
        "audio": [["EAC3", 141], ["AC3", 98], ["DTS", 61], ["TrueHD Atmos", 44], ["AAC", 39], ["DTS-HD MA", 15]],
        "hdr": [["SDR", 301], ["HDR10", 52], ["DV HDR10", 31], ["HDR10Plus", 14]], "channels": [["6", 244], ["8", 87], ["2", 67]],
        "decades": [["1960s", 4], ["1970s", 9], ["1980s", 31], ["1990s", 58], ["2000s", 92], ["2010s", 141], ["2020s", 77]],
        "genres": [["Drama", 188], ["Action", 152], ["Thriller", 121], ["Comedy", 117], ["Adventure", 98], ["Science Fiction", 84], ["Crime", 77], ["Fantasy", 52], ["Horror", 41], ["Animation", 38]],
        "studios": [["Warner Bros. Pictures", 34], ["Universal Pictures", 29], ["A24", 18], ["Paramount", 17], ["Netflix", 14]],
        "certification": [["PG-13", 141], ["R", 133], ["PG", 61], ["12", 31]],
        "largest": [{"title": n, "year": y, "size": int((80 - i * 7) * GB)} for i, (n, y, *_) in enumerate(MOVIES)],
        "monitored": 401, "total": 412, "have": 398, "avg_size": int(9.8 * GB), "runtime": 398 * 118,
        "added": [[(today - timedelta(days=30 * i)).strftime("%Y-%m"), rnd.randint(4, 30)] for i in range(23, -1, -1)],
        "roots": [{"path": "/data/media/movies", "free": 2700 * GB, "total": 16000 * GB}]}}
    show = {"version": "4.0.11", "library": p["sonarr"]["library"], "missing": 30, "nerd": {
        "status": [["continuing", 31], ["ended", 29], ["upcoming", 4]], "networks": [["HBO", 9], ["Netflix", 8], ["BBC One", 6], ["Apple TV+", 5], ["AMC", 4]],
        "types": [["standard", 58], ["anime", 6]], "seasons": 241, "complete": 44, "runtime": 2210 * 47,
        "decades": [["1990s", 3], ["2000s", 11], ["2010s", 29], ["2020s", 21]],
        "genres": [["Drama", 44], ["Comedy", 21], ["Crime", 17], ["Sci-Fi & Fantasy", 15], ["Mystery", 12], ["Animation", 7]],
        "largest": [{"title": n, "year": 2020 + i, "size": int((420 - i * 60) * GB)} for i, (n, *_) in enumerate(SHOWS)],
        "monitored": 58, "total": 64, "have": 64, "avg_size": int(80 * GB),
        "added": [[(today - timedelta(days=30 * i)).strftime("%Y-%m"), rnd.randint(0, 5)] for i in range(23, -1, -1)],
        "roots": [{"path": "/data/media/tv", "free": 2700 * GB, "total": 16000 * GB}]}}
    return {
        "range": rng if rng in ranges else "24h", "now": int(now * 1000), "title": "demo", "version": "demo",
        "series": {"step": step, "t": t, "s": s}, "days": days, "since": int((now - 400 * 86400) * 1000),
        "clients": {"sabnzbd": "SABnzbd", "qbittorrent": "qBittorrent"}, "live": p["history"],
        "system": {**p["system"], "kernel": "6.8.12-4-pve"}, "proxmox": {"ok": True, "guests": guests}, "disks": p["disks"], "network": p["network"],
        "services": svc, "sab": p["sab"],
        "downloads": {"sabnzbd": {"ok": True, "client": "SABnzbd", "version": "4.3.3", "speed": p["history"]["sab"][-1], "queueTotal": 12, "failed": 0,
                                  "totals": {"day": 38 * GB, "week": 212 * GB, "month": 840 * GB, "total": 9800 * GB}},
                      "qbittorrent": {"ok": True, "client": "qBittorrent", "version": "v5.0.2", "speed": 2.1 * 1048576, "queueTotal": 3, "failed": 1,
                                      "totals": {"day": 7 * GB, "week": 39 * GB, "month": 150 * GB, "total": 2100 * GB}}},
        "library": {"radarr": movie, "sonarr": show},
        "prowlarr": {"version": "1.24.3", "indexers": 6, "stats": [
            {"name": n, "queries": q, "grabs": g, "failed": f, "ms": ms} for n, q, g, f, ms in
            [("NZBgeek", 4120, 611, 12, 340), ("DrunkenSlug", 3890, 402, 31, 520), ("1337x", 2210, 140, 88, 910), ("TorrentLeech", 1980, 233, 9, 280),
             ("Nyaa", 760, 51, 4, 410), ("Milkie", 540, 22, 1, 300)]]},
        "events_total": sum(sum(d["kinds"].values()) for d in days),
        "jellyfin": {"ok": True, "version": "10.10.3", "server": "media", "profiles": PEOPLE,
                     "library": {"movies": 412, "series": 64, "episodes": 2210, "songs": 5120, "albums": 410, "books": 0},
                     "watched": [{"who": PEOPLE[i % 3], "title": f"{SHOWS[i % 4][0]} {SHOWS[i % 4][1]}", "pct": 100, "when": int((now - (i + 1) * 3600) * 1000)} for i in range(6)]},
    }
