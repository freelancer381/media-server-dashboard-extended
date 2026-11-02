"""Made-up data for `python3 server.py --demo`: the same shape as the real thing, nothing read from anywhere.

Everything moves with the clock, so the graphs scroll and the workers come and go like they would for real.
"""
import html
import json
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


def payload(title):
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
    queue = [
        {"id": f"demo{i}", "title": f"{n}.{y}.1080p.WEB-DL", "name": f"{n} ({y})", "pct": max(0.0, round(wave(now, 20, 20, 95, i) - i * 25, 1)),
         "left": f"0:{12 + i * 7:02d}:00", "size": f"{3 + i}.{i} GB", "state": "Downloading" if i == 0 else "Queued"}
        for i, (n, y, *_ ) in enumerate(MOVIES[:5])
    ]
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
    names = ["Home Cinema", "Radarr", "Sonarr", "Bazarr", "Prowlarr", "SABnzbd"]
    tasks = {**activity, "SABnzbd": f"Downloading {queue[0]['name']} · {round(queue[0]['pct'])}%",
             "Home Cinema": f"Streaming {streams[0]['title']} to {watching}" if streams else None}
    nxt = [{"title": q["name"], "pct": round(q["pct"]), "size": q["size"]} for q in queue]
    services = [{"name": n, "up": True, "issues": [], "task": tasks.get(n), "next": nxt if n in ("SABnzbd", "Radarr") else []} for n in names]

    poster = lambda kind, i: f"/demo-poster/{kind}{i}.svg"
    return json.dumps({
        "title": title, "now": ms, "alerts": [],
        "history": {"t": [int(t * 1000) for t in ts], "cpu": cpu, "mem": mem, "rx": rx, "tx": tx, "disk": disk, "sab": sab},
        "upcoming": [{"kind": "Episode", "title": f"{SHOWS[i][0]} S0{i + 1}E0{i + 4}", "when": iso(ms + (i + 1) * 86_400_000)} for i in range(3)]
        + [{"kind": "Movie", "title": MOVIES[5][0], "when": iso(ms + 5 * 86_400_000)}],
        "system": {"host": "pve-demo", "cpu_name": "AMD Ryzen 7 5700G", "cores": 16, "pve": "pve-manager/8.3.0", "booted": ms - 3 * 86_400_000 - 7_200_000,
                   "speed_limit": 100, "cpu": {"user": cpu[-1] * 0.7, "system": cpu[-1] * 0.3, "idle": 100 - cpu[-1]}, "load": [2.1, 1.8, 1.6],
                   "memory": {"total": 64 * GB, "used": int(mem[-1] / 100 * 64 * GB), "free_level": 40, "swap_total": 0, "swap_used": 0}},
        "services": services,
        "cinema": {"enabled": True, "ok": True, "converting": 0, "profiles": PEOPLE, "streams": streams, "watched": watched, "missing_drives": []},
        "sab": {"ok": True, "version": "4.3.3", "paused": False, "speed": sab[-1], "left": 21 * GB, "eta": "0:34:10", "queueTotal": 12, "queue": queue, "failed": 0,
                "totals": {"day": 38 * GB, "week": 212 * GB, "month": 840 * GB, "total": 9800 * GB}},
        "radarr": {"ok": True, "version": "5.0", "library": {"total": 412, "have": 398, "size": 3900 * GB}, "missing": 14,
                   "recent": [{"title": n, "year": y, "poster": poster("m", i), "size": 8 * GB, "quality": "Bluray-1080p", "when": iso(ms - (i + 1) * 5_400_000)} for i, (n, y, *_) in enumerate(MOVIES)],
                   "upcoming": [], "health": [], "roots": [{"path": "/data/media/movies", "ok": True}]},
        "sonarr": {"ok": True, "version": "4.0", "library": {"total": 64, "have": 2210, "episodes": 2380, "size": 5100 * GB}, "missing": 30,
                   "recent": [{"title": n, "sub": e, "poster": poster("s", i), "quality": "WEBDL-1080p", "when": iso(ms - (i + 1) * 7_200_000)} for i, (n, e, *_) in enumerate(SHOWS)],
                   "upcoming": [], "health": [], "roots": [{"path": "/data/media/tv", "ok": True}]},
        "prowlarr": {"ok": True, "version": "1.0", "indexers": 6, "enabled": 6, "health": []},
        "bazarr": {"ok": True, "health": []},
        "activity": activity,
        "network": {"lan": "", "vpn": False, "online": True, "ping": 14.0},
        "disks": [{"name": "local-zfs", "total": 900 * GB, "free": 610 * GB}, {"name": "media", "total": 16000 * GB, "free": 2700 * GB}],
        "proxmox": {"ok": True, "guests": []},
        "home": {"enabled": True, "side": "right", "layout": "grid", "tiles": [
            {"name": n, "url": "", "icon": ic, "check": f"192.168.1.{20 + i}:{port}", "up": True, "ms": 1 + i % 3,
             "guest": {"id": 101 + i, "name": n.lower(), "type": "lxc", "status": "running", "cpu": round(wave(now, 30 + i * 7, 1, 35, i), 1),
                       "mem": int(wave(now, 90, 0.3, 1.6, i) * GB), "maxmem": 2 * GB, "rx": int(rx[-1] * (0.6 if n == "SABnzbd" else 0.05)),
                       "tx": int(tx[-1] * 0.2), "uptime": 86_400 * (3 + i)}}
            for i, (n, ic, port) in enumerate([("Radarr", "🎬", 7878), ("Sonarr", "📺", 8989), ("SABnzbd", "📥", 8080), ("Jellyfin", "🍿", 8096)])
        ] + [{"name": "Home Assistant", "url": "", "icon": "🏠", "check": "192.168.1.40:8123", "up": True, "ms": 2, "guest": None},
             {"name": "NAS", "url": "", "icon": "🗄️", "check": "192.168.1.5:5000", "up": False, "ms": None, "guest": None}]},
    })


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
