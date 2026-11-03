# Installing on Proxmox VE (LXC)

The recommended way: the dashboard gets a small Debian container of its own. A script on the Proxmox
host asks what it needs, creates the container and installs the dashboard together with Caddy
(reverse proxy, HTTPS) and the update service.

**You need:** Proxmox VE 8 or newer, root on the host, internet access for the new container.

## 1. Run the script

On the Proxmox host (shell in the web UI or over SSH), as root:

```bash
apt install -y git whiptail
git clone https://github.com/freelancer381/media-server-dashboard-extended.git /root/media-server-dashboard
bash /root/media-server-dashboard/proxmox/install-lxc.sh
```

## 2. The questions

| Question | What it means | Suggestion |
|---|---|---|
| Container ID | ID of the new LXC | next free one |
| Hostname | name on the network | `media-dashboard` |
| **Disk** | which storage the container lives on. Every active storage for containers (`rootdir`) is offered, with its free space | your SSD storage, e.g. `local-zfs` / `local-lvm` |
| Template | where the Debian template is kept (only asked if there are several) | `local` |
| Disk size / CPU / Memory | size of the container | 4 GB / 1 core / 512 MB |
| Network / VLAN | bridge and an optional VLAN tag | `vmbr0` |
| IP address | DHCP or a static address with gateway | static, so webhooks know where to go |
| Proxmox access | creates the user `dashboard@pve` with an API token, role `PVEAuditor` (read-only), and fills it into the wizard together with the host's certificate | yes |

The script installs the **newest signed release**: inside the container it checks the release's signature
(this repository's release workflow at that tag, with cosign) and the archive's SHA-256 before anything is
installed, exactly like the in-app updater. To install your checkout instead (development), run it with
`MSD_FROM_CHECKOUT=1`.

The script then creates an **unprivileged** Debian container (nesting on, start at boot on), installs
everything and ends with:

```
 Open:         http://192.168.1.50/setup
 Setup token:  1a2b3c4d-5e6f7a8b
```

## 3. Wizard

Open `http://<container-ip>/setup`, enter the setup token, then:

1. **Account:** user name and password (at least 10 characters).
2. **Address:** IP only, or your own domain with HTTPS:
   - *Let's Encrypt*: the domain points at the container publicly, ports 80 and 443 are reachable.
   - *Internal CA*: for names on your network only (`dashboard.home.lan`). To stop browser warnings, import Caddy's root certificate: `/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt` in the container.
3. **Proxmox:** already filled in with the script's token. "Test connection", then pick the storages to show and the guests to watch.
4. **Apps:** tick them, enter the URL (as the container sees it, e.g. `http://192.168.1.21:7878`) and the API key, "Test".
5. **Start page**, **Live feed** (webhook addresses for the apps), **Done** → Save.

## Behind Nginx Proxy Manager or another proxy

In the wizard's **Address** step choose **"Behind my own reverse proxy"** and enter your public domain (used
for links such as the webhook addresses). Then point the proxy at the container's IP, scheme `http`, port
**80** (Caddy), and add `proxy_buffering off;` for the live feed.

- **502 Bad Gateway:** port 8900. The dashboard only listens on 127.0.0.1:8900 inside the container; use 80.
- **Too many redirects:** the address step is set to "Your own domain, HTTPS from this server". Then Caddy
  redirects to HTTPS itself and loops with your proxy's HTTPS. Choose "Behind my own reverse proxy" (open
  `http://<container-ip>/setup` to change it).

In this mode the login lockout sees the real visitor's address and the login cookie is marked secure. See
[docker.md](docker.md#nginx-proxy-manager) for the proxy settings.

## Updates

**Settings → Updates**: "Check for updates" shows the newest signed release with its notes. Confirm
with your password and the update service installs it; progress and log update live. Optionally it
installs new releases automatically at a set hour.

What happens: check the signature of `release.json` (Sigstore, this repository's release workflow at
the tag), check the release policy, download the archive and compare its SHA-256, back up the settings
(`/var/lib/media-server-dashboard-updater/backups`, the last 3), unpack the new version next to the old
one, switch over, restart, health check. If anything fails, the previous version runs again.

For this the container needs outgoing HTTPS to `api.github.com`, `github.com` and GitHub's download
hosts (`*.githubusercontent.com`), and to `tuf-repo-cdn.sigstore.dev` (the trust root cosign checks
signatures with). If a firewall blocks them, Settings → Updates reports "invalid signature" with the
reason.

By hand, in the container:

```bash
python3 /usr/local/lib/media-server-dashboard/msd-updater.py check
python3 /usr/local/lib/media-server-dashboard/msd-updater.py status
python3 /usr/local/lib/media-server-dashboard/msd-updater.py install 0.3.0
```

The update service itself (and the install scripts) is updated by running the installer again from
the host. A release that needs a newer update service says so in the settings:

```bash
cd /root/media-server-dashboard && git pull
bash proxmox/install-lxc.sh --update <CTID>
```

## Creating the API token by hand

If you answered "no" in the script:

```bash
pveum user add dashboard@pve --comment "Media Server Dashboard (read-only)"
pveum acl modify / --users dashboard@pve --roles PVEAuditor
pveum user token add dashboard@pve msd --privsep 1
pveum acl modify / --tokens 'dashboard@pve!msd' --roles PVEAuditor
```

In the wizard: URL `https://<host-ip>:8006`, token ID `dashboard@pve!msd`, and the secret shown. To
check the certificate, copy `/etc/pve/pve-root-ca.pem` from the host into the container
(`pct push <CTID> /etc/pve/pve-root-ca.pem /var/lib/media-server-dashboard/pve-root-ca.pem`) and enter that path.

## What lives where (in the container)

| Path | Contents |
|---|---|
| `/opt/media-server-dashboard/current` | symlink to the running version in `releases/` |
| `/var/lib/media-server-dashboard/config.json` | settings including API keys (readable by `msd` only) |
| `/var/lib/media-server-dashboard/{events,game}.json` | live feed and points |
| `/etc/media-server-dashboard/updater.json` | where updates come from (root) |
| `/etc/caddy/msd/site.caddy` | the Caddy site written by the wizard |

Services: `media-server-dashboard`, `caddy`, `msd-updater.path`, `msd-updater-check.timer`.
Logs: `journalctl -u media-server-dashboard -u msd-updater -f`

## Forgot the password

Stop the service, remove the `"auth"` block from `/var/lib/media-server-dashboard/config.json`, start it
again: the wizard opens with a new setup token (`journalctl -u media-server-dashboard`), all other
settings stay.

## Removing it

```bash
pct stop <CTID> && pct destroy <CTID>
pveum user delete dashboard@pve
```
