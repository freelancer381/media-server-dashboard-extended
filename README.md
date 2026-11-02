# Media Server Dashboard · Proxmox edition

A dashboard for a Proxmox homelab that runs a media stack, plus a 3D office where your apps are the employees.
It runs in its own small LXC and watches everything over the network: the Proxmox host through its API
(read-only token) and Radarr, Sonarr, Prowlarr, Bazarr, SABnzbd and any other app through their APIs,
wherever they run (other LXCs, Docker, VMs).

- **Dashboard** (`/`): host CPU, memory, network and download graphs, which services are reachable (hover one to see what's wrong), the SABnzbd queue, Proxmox storages, recently added movies and shows, and what's coming up in the next 14 days.
- **Office** (`/office`): the apps sit at their desks while they're working and wander off to the break room when they're idle. Custom apps join as extra staff. Parcels pile up with the download queue, posters hang on the wall, the data center racks light up with host CPU, memory and network use.
- **Start page** (optional, beside the office, left or right): your own tiles or a table with icons, links and live numbers (online/latency, and for linked Proxmox guests CPU, RAM, ↓/↑ and uptime), with the host's CPU, RAM, load and traffic on top.
- **Setup wizard** (`/setup`): user and password, address (IP or your own domain with HTTPS), Proxmox connection with a test button, apps by checkbox with a test button each, custom apps (presets for Lidarr, Readarr, Whisparr, Jellyseerr, Jellyfin, Plex, Tautulli, qBittorrent), and the start page. Later it's the settings page (⚙ Settings).

Standard library Python only, no build step, no accounts, no analytics. The only outside requests are poster images from TMDB/TheTVDB that Radarr and Sonarr already point to.

## Install on Proxmox

On the Proxmox host, as root:

```bash
git clone <this repository> /root/media-server-dashboard
bash /root/media-server-dashboard/proxmox/install-lxc.sh
```

It asks for the container ID, hostname, **which storage to install on**, disk/CPU/RAM, bridge, VLAN and DHCP or a static IP,
and whether to create a read-only API token (`dashboard@pve!msd`, role `PVEAuditor`). It then creates an unprivileged Debian
container, installs the dashboard with Caddy in front of it, and prints the address and a one-time **setup token**.
Open `http://<container-ip>/setup`, enter the token and go through the wizard. With the API token option the Proxmox step is
already filled in (including the host's CA, so the certificate is checked).

Update later with `bash proxmox/install-lxc.sh --update <CTID>`.

### Domain and HTTPS

Choose "own domain" in the wizard's address step:

- **Let's Encrypt**: the domain must point at the container and ports 80/443 must be reachable from the internet.
- **Internal CA**: for names only on your network (e.g. `dashboard.home.lan`). Browsers warn until you trust Caddy's root certificate (`/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt` in the container).

The wizard writes the Caddy site; Caddy reloads automatically. Plain `http://<ip>` access can stay on as a fallback.

## Install by hand (any Debian/Ubuntu box or LXC)

```bash
git clone <this repository> /opt/media-server-dashboard
bash /opt/media-server-dashboard/scripts/install-in-container.sh
```

Or without Caddy and systemd: `python3 server.py`, then open `http://<host>:8900/setup` (the setup token is printed in the log).

## Try it with made-up data

```bash
python3 server.py --demo
```

Then open http://localhost:8900.

## Security notes

- Everyone logs in (user and password, PBKDF2-hashed). Behind the bundled Caddy the dashboard only listens on `127.0.0.1`.
- Before the first setup, the wizard only opens with the setup token. Afterwards it needs a signed-in user.
- API keys and the Proxmox token are stored in `/var/lib/media-server-dashboard/config.json` (mode 600) and never sent to the browser. The wizard only shows whether one is set.
- Use the read-only `PVEAuditor` role for the Proxmox token. The dashboard never changes anything on the host or in the apps.
- Don't expose it to the internet without HTTPS. Repeated wrong logins lock the client out for five minutes.

## Notes

- Host network traffic is the sum of the guests' traffic as Proxmox reports it (refreshed about every 10 s).
- The network meters in the office read full at 100 Mbit/s.
- The 3D office uses [three.js](https://threejs.org) (MIT), bundled in `vendor/`.

## License

MIT. Based on [Essq1337/media-server-dashboard](https://github.com/Essq1337/media-server-dashboard) (MIT).
