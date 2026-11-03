# Changelog

All notable changes. Versions follow [Semantic Versioning](https://semver.org). The section of a release
becomes its GitHub release text, so write it for the people who run the dashboard.

## Unreleased

Added:
- **Statistics** (📊 Stats, `/stats`): graphs and numbers kept for up to two years, a point per minute for a day, per hour for 90 days and per day after that. Covers CPU, memory, load, network and disk I/O with average and peak; download speed per client, gigabytes per day and queue length; every app's response time; Prowlarr's indexers (queries, grabs, failures, response time); the live feed per day and a weekday × hour heatmap; and the library in detail (qualities, codecs, HDR, audio, genres, decades, studios, the biggest titles, added per month). Saved in `stats.json` in the data folder.
- **Full screen and kiosk mode** for wall tablets on the dashboard, the office and the statistics: "⛶ Full screen" or the F key. In kiosk mode (`?kiosk` in the address, full screen, or as an app on the home screen) the screen stays on, the pointer hides, settings and sign-out are hidden and the page refreshes once a night. The dashboard can be added to the home screen and opens full screen on the page last used.
- **Docker and Synology without Proxmox** show the machine they run on: CPU, memory, swap, load, disk I/O, uptime and the free space of the mounted folders, from `/proc` (network: the container's own, or the host's with `network_mode: host`).
- qBittorrent and rdtclient now show real numbers for today, this week and this month: the dashboard counts them itself.
- qBittorrent can report a finished torrent instantly: "Run external program on torrent finished" with `curl -fsS --data-urlencode "torrent=%N" <webhook URL>` (details in Settings → Live feed).
- `MSD_TRUSTED_PROXIES` and `trusted_proxies` take ranges such as `172.16.0.0/12`, so a Docker network created again with a new gateway keeps working.
- The demo (`--demo`) has qBittorrent next to SABnzbd, and the README has screenshots.

Fixed:
- Setup wizard: "Behind my own reverse proxy" told Docker installs to forward to port 80, which only applies to the Proxmox LXC (Caddy). In Docker it now says the published port (8900) and to set `MSD_TRUSTED_PROXIES`; the README has a table for both.

Changed:
- Office: the view goes down to almost level and zooms in further, towards the pointer, so the boards and posters on the walls can be read up close. Move it with the right mouse button or two fingers; "Reset view" brings back the whole room.
- Office: more to do on a break. A table football table, a reading corner with two armchairs, and the two bean bags that were only furniture so far. Everyone on a break gets a spot of their own, and the games are only played in pairs: nobody plays ping pong alone any more. When a partner goes back to work, the other one finds a seat.

## 0.4.1 - 2026-11-03

Fixed:
- Updates were refused with "invalid signature: … release.json: permission denied" since 0.3.5. The signature check runs as an unprivileged user, which couldn't reach its files inside the update service's folder. It now can, and still sees nothing else there (update service 1.3.1).
- Because the update service itself can't check releases until then, update to this release from the Proxmox host once: `cd /root/media-server-dashboard && git pull`, then `bash proxmox/install-lxc.sh --update <CTID>`.

## 0.4.0 - 2026-11-03

Added:
- qBittorrent and rdtclient (Real-Debrid / AllDebrid) as download clients: speed, queue, finished and failed downloads in the live feed, Radarr's and Sonarr's titles for each torrent. rdtclient speaks qBittorrent's Web API, so both sign in with user and password (or none, with qBittorrent's "Bypass authentication" for the dashboard's address).
- Several download clients at once, for example SABnzbd plus qBittorrent or rdtclient plus qBittorrent: the download card, speed graph and parcel pile add them up, each worker in the office shows its own queue, and pauses and failures are reported per client.

Changed:
- If you added qBittorrent as a custom app before, switch on the built-in one under Settings → Apps and remove the custom one (both can't have the same name). qBittorrent's preset for custom apps is gone.
- qBittorrent keeps no per-day numbers, so "Today", "This week" and "This month" show "–" for it; "All time" comes from qBittorrent.

## 0.3.5 - 2026-11-03

Security (second audit):
- Saved API keys and the Proxmox token were reused for a changed address, so an open session could send them to any server (connection test or saving). They are now only reused for the same server; a new address needs them again.
- Sessions are known to the server: each sign-in gets its own, signing out ends it on the server, a new password ends all of them, "Sign out everywhere" in the account settings, and they expire after 30 days unused (180 at most). Open live streams close when their session ends.
- The app can no longer write Caddy's configuration: a root helper (`msd-caddy.py`) writes the site from the checked settings.
- The update service runs cosign as `nobody` in a folder of its own, and is sandboxed by systemd; the app's sandbox is stricter (falls back automatically where a container can't apply it).
- New installs and `install-lxc.sh --update` take the newest signed release and check it like the updater does (`MSD_FROM_CHECKOUT=1` for this checkout instead).
- `/api/open` only redirects to plain http(s) addresses and encodes what a media server returns.

Run `install-lxc.sh --update <CTID>` once on the Proxmox host for the Caddy helper, the new sandbox and the signed install path.

## 0.3.4 - 2026-11-03

Security (from an audit):
- A negative `Content-Length` made the server read and buffer everything until the connection ended, without logging in (memory exhaustion); it is now refused with 400.
- Connections that send nothing more are closed after 20 seconds, and at most 200 requests are handled at a time, instead of one thread each, kept forever.
- Pages carry a Content-Security-Policy (only this server's own scripts, connections and frames; posters from TMDB/TheTVDB).
- Setting a new password needs the current one, so an unlocked browser alone can't take the account.
- Demo mode, which has no login, no longer makes the connection tests' network requests for visitors.
- New passwords are hashed with 600,000 PBKDF2 rounds (OWASP 2023); existing ones keep working.
- The list of failed logins is pruned instead of growing with every address.

## 0.3.3 - 2026-11-03

- The project moved to github.com/freelancer381/media-server-dashboard-extended; images are now `ghcr.io/freelancer381/media-server-dashboard-extended`.
- The update service follows a moved repository (GitHub forwards it) and checks signatures against the repository's current name, so installations keep updating after the move.

- HEAD requests are answered like GET without a body (`curl -I`, uptime monitors) instead of "501 Not Implemented".

## 0.3.2 - 2026-11-03

- Address: a new option "Behind my own reverse proxy" (Nginx Proxy Manager, Traefik, Synology …). The container then serves plain HTTP without redirects, so the proxy's HTTPS no longer loops ("too many redirects"); the login lockout sees the real visitor and the cookie is marked secure.

## 0.3.1 - 2026-11-03

- Office: when the Proxmox host can't be reached, the server catches fire and the staff run around shouting; they calm down and go back when it answers again.
- Posters on the dashboard and in the office open the title in Plex or Jellyfin (set up as a custom app), otherwise in Radarr or Sonarr.
- Live feed: dismiss single messages or clear them all, in the office and in a new Live card on the dashboard; dashboard warnings can be dismissed until the problem comes back.
- After saving the settings or finishing the setup, the page and every open dashboard or office tab reload by themselves once the server is back (no more F5).
- No more German leftovers: old feed lines from before 0.2.1 are dropped, an older update service's messages show in English, dates and times use an English format.
- The update service now updates itself from verified releases.

## 0.3.0 - 2026-11-02

- NZBGet as a download client next to SABnzbd (JSON-RPC with user and password), with an extension for instant live-feed messages.
- Settings: tabs instead of the wizard once set up, a save bar on every tab when something changed.
- "Version X available" on the dashboard and in the office, with a one-time heads-up per version.
- Docs: Nginx Proxy Manager (and why port 8900 on the LXC gives a 502).

- Office: click a worker, its name tag or its speech bubble to open that app (optional separate browser address per app in the settings).
- Office: every worker has a place of its own; from the seventh on they work at the whiteboard and get their own break spots, so nobody stands inside anybody else. Called back halfway, workers retrace their steps instead of walking through desks.
- Office: name tags and bubbles stay inside the view and step aside instead of covering each other; the live feed moved to the empty top-left corner.
- Start page: tiles and the table update in place; numbers glide, bars ease, new tiles fade in.
- Graceful restarts: the server saves and closes on stop (also as PID 1 in Docker), pages show "Reconnecting …" and reload once when a newer version is running.
- Dashboard: tidier header, focus outlines, smoother meters; animations respect "reduce motion".

## 0.2.1 - 2026-11-02

- Everything is in English now: the setup wizard and settings, the live feed, the start page, update messages, the README and the guides.

- The release workflow now also verifies the container image's signature before publishing.
- Docs: which hosts a firewall must allow for updates and image pulls.

## 0.2.0 - 2026-11-02

First release of the extended edition.

- **Proxmox:** host stats, storages and guests through the Proxmox API (read-only `PVEAuditor` token); the apps are reached over the network by URL and API key.
- **Setup wizard** at `/setup`, later the settings: user, address (IP or domain with HTTPS through Caddy), Proxmox, apps with connection tests, custom apps with presets, start page, live feed, updates.
- **Start page** beside the office: tiles or a table with your own icons and links, online checks and per-guest CPU, RAM and traffic.
- **Live feed:** webhooks from Radarr, Sonarr, Lidarr, Readarr, Prowlarr, Bazarr and SABnzbd (Apprise), Jellyfin and your own tools, plus what the dashboard notices itself, as a chat in the office. Pages update live over Server-Sent Events.
- **Office game:** XP, levels, badges, today's numbers and a streak of days without errors for every worker.
- **One-click updates** from Settings → Updates: only releases signed by this repository's release workflow are installed, with a backup, a health check and an automatic rollback.
- **Installer** for Proxmox (`proxmox/install-lxc.sh`): creates the LXC with your choice of storage, CPU, RAM and network and installs everything.
