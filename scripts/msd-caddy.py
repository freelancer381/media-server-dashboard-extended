#!/usr/bin/env python3
"""Writes the dashboard's Caddy site from its settings (runs as root, started by msd-caddy.path).

The app itself can't write Caddy's configuration: it only saves its settings. This reads the few fields that
decide the site (address mode, domain, HTTPS mode, port), checks each of them, writes
/etc/caddy/msd/site.caddy and reloads Caddy if the site changed. Nothing else from the settings reaches Caddy.
Standard library only.

    msd-caddy.py [settings.json] [site.caddy]
"""
import json
import os
import re
import subprocess
import sys

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")


def render(c):
    """The site for these settings. Kept identical to server.caddy_site (a test checks it)."""
    port = int(c.get("port") or 8900)
    up = f"reverse_proxy 127.0.0.1:{port}"
    if c.get("behind_proxy"):
        return ("# Written by the dashboard's setup wizard; changes here are overwritten.\n"
                "# Behind your own reverse proxy: HTTP only, no redirects.\n"
                f"http:// {{\n    {up} {{\n"
                "        header_up X-Msd-Peer {http.request.remote.host}\n"
                "        header_up X-Msd-Forwarded-For {http.request.header.X-Forwarded-For}\n"
                "        header_up X-Msd-Forwarded-Proto {http.request.header.X-Forwarded-Proto}\n"
                "    }\n}\n")
    blocks = []
    if c.get("domain"):
        tls = "\n    tls internal" if c.get("tls") == "internal" else ""
        blocks.append(f"{c['domain']} {{\n    {up}{tls}\n}}")
    if not c.get("domain") or c.get("ip_access", True):
        blocks.append(f"http:// {{\n    {up}\n}}")
    return "# Written by the dashboard's setup wizard; changes here are overwritten.\n" + "\n\n".join(blocks) + "\n"


def checked(raw):
    """Only the fields the site needs, each validated; anything odd falls back to plain HTTP by IP."""
    c = {}
    try:
        port = int(raw.get("port") or 8900)
    except (TypeError, ValueError):
        port = 8900
    c["port"] = port if 1 <= port <= 65535 else 8900
    domain = str(raw.get("domain") or "").strip().lower().rstrip(".")
    c["domain"] = domain if DOMAIN_RE.match(domain) else ""
    c["tls"] = "internal" if raw.get("tls") == "internal" else "public"
    c["ip_access"] = raw.get("ip_access", True) is not False
    c["behind_proxy"] = raw.get("behind_proxy") is True
    return c


def read_settings(path):
    """The app's settings file belongs to the app user: open it without following links, and only so much."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return {}
    with os.fdopen(fd, "rb") as f:
        data = f.read(2_000_000)
    try:
        value = json.loads(data)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def main(argv):
    settings = argv[1] if len(argv) > 1 else "/var/lib/media-server-dashboard/config.json"
    site = argv[2] if len(argv) > 2 else "/etc/caddy/msd/site.caddy"
    text = render(checked(read_settings(settings)))
    try:
        with open(site, encoding="utf-8") as f:
            if f.read() == text:
                return 0  # nothing changed: no reload
    except OSError:
        pass
    tmp = site + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, site)
    if os.environ.get("MSD_CADDY_NO_RELOAD") != "1":
        subprocess.run(["systemctl", "reload-or-restart", "caddy"], check=False)
    print(f"Caddy site updated: {site}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
