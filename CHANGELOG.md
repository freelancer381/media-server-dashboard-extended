# Changelog

All notable changes. Versions follow [Semantic Versioning](https://semver.org). The section of a release
becomes its GitHub release text, so write it for the people who run the dashboard.

## Unreleased

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
