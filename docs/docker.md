# Installing with Docker

The image `ghcr.io/freelancer381/media-server-dashboard-extended` holds just Python and the dashboard
(amd64 and arm64). Every release image is signed by the release workflow (Sigstore).

## Docker Compose

```bash
mkdir media-dashboard && cd media-dashboard
curl -fsSLO https://raw.githubusercontent.com/freelancer381/media-server-dashboard-extended/main/docker-compose.yml
mkdir data && sudo chown 1000:1000 data
docker compose up -d
docker compose logs dashboard     # shows the setup token
```

Then open `http://<host>:8900/setup` and enter the token.

The `docker-compose.yml`:

```yaml
services:
  dashboard:
    image: ghcr.io/freelancer381/media-server-dashboard-extended:latest   # or a fixed version, e.g. :0.2
    container_name: media-server-dashboard
    restart: unless-stopped
    ports:
      - "8900:8900"
    volumes:
      - ./data:/data
    environment:
      TZ: Europe/Berlin
      # MSD_TRUSTED_PROXIES: "172.18.0.1"   # only behind a reverse proxy, see below
```

Without Compose:

```bash
docker run -d --name media-server-dashboard --restart unless-stopped \
  -p 8900:8900 -v "$PWD/data:/data" -e TZ=Europe/Berlin \
  ghcr.io/freelancer381/media-server-dashboard-extended:latest
```

## Settings through environment variables

| Variable | Default | Meaning |
|---|---|---|
| `MSD_PORT` | `8900` | port inside the container |
| `MSD_TRUSTED_PROXIES` | empty | comma-separated addresses of your reverse proxy, as the container sees them |
| `MSD_UPDATE_REPO` | `freelancer381/media-server-dashboard-extended` | where to look for new releases (a notice only) |
| `TZ` | UTC | time zone for the feed and today's numbers |

Everything else is set in the wizard and stored in `/data/config.json`.

## Network

- Enter the apps in the wizard with their LAN address (`http://192.168.1.21:7878`). If they run in the same Compose project, the service name works too (`http://radarr:7878`).
- For **webhooks** the apps must reach the dashboard: `http://<docker-host>:8900/hook/…`. The ready-made addresses are under Settings → Live feed.
- Proxmox (optional): URL `https://<proxmox-ip>:8006` and a token with `PVEAuditor` (see [proxmox.md](proxmox.md#creating-the-api-token-by-hand)). Without a CA file, switch off certificate checking in the wizard, or put `pve-root-ca.pem` into `/data` and enter `/data/pve-root-ca.pem`.

## Behind a reverse proxy (HTTPS)

Nginx Proxy Manager, Traefik, Caddy or Synology: just forward to `http://<host>:8900`.

- **Important for the live feed:** the proxy must not buffer Server-Sent Events (`/api/stream`). Nginx honours the `X-Accel-Buffering: no` header the dashboard sends; Traefik and Caddy need nothing.
- Set `MSD_TRUSTED_PROXIES` to the address the proxy connects from (with port mapping usually the gateway of the Docker network, e.g. `172.18.0.1`; `docker network inspect <network>` shows it). Then the login lockout hits the real visitor instead of the proxy, and the login cookie is marked `Secure` over HTTPS.

### Nginx Proxy Manager

Add a proxy host with the domain, scheme `http`, and as forward hostname/port:

| Dashboard runs … | Forward hostname | Forward port |
|---|---|---|
| in the LXC from the Proxmox installer | the container's IP | **80** (Caddy; the dashboard itself only listens on 127.0.0.1:8900) |
| in Docker on another host | that host's IP | 8900 |
| in Docker next to NPM, same Docker network | `media-server-dashboard` | 8900 |
| in Docker next to NPM, different networks | the host's LAN IP (not `localhost`, which is NPM itself) | 8900 |

A **502 Bad Gateway** means NPM can't reach that address: usually port 8900 on the LXC (use 80), `localhost`
as hostname, or `https` as scheme. Under *Advanced*, add `proxy_buffering off;` so the live feed arrives at
once. With the LXC, choose "By IP address only" in the wizard, since NPM does the HTTPS.

## Updates

Settings → Updates says when a new version is out. To update:

```bash
docker compose pull && docker compose up -d
```

Settings, feed and points stay in `./data`. A fixed version tag (`:0.2`) only gets fixes of that series.

Restarts are graceful: on `docker stop` the dashboard tells open pages, saves the feed and the points and
exits within a second. Open pages show "Reconnecting …", connect again by themselves and reload once if
a newer version is now running, so nobody has to refresh by hand.

To update automatically, a tool that pulls new images and recreates the container works well with that,
for example Watchtower (`--label-enable`; the Compose file already carries its label). Use a fixed
series tag such as `:0.2` if you only want fixes to arrive automatically.

Pulling the image needs access to `ghcr.io` and `pkg-containers.githubusercontent.com`; `cosign verify`
also needs `tuf-repo-cdn.sigstore.dev`.

Checking the image signature:

```bash
cosign verify ghcr.io/freelancer381/media-server-dashboard-extended:0.2.0 \
  --certificate-identity "https://github.com/freelancer381/media-server-dashboard-extended/.github/workflows/release.yml@refs/tags/v0.2.0" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Building it yourself

```bash
git clone https://github.com/freelancer381/media-server-dashboard-extended.git && cd media-server-dashboard-extended
docker build --build-arg VERSION="$(cat VERSION)" -t media-server-dashboard .
```

Then replace `image:` with `build: .` in `docker-compose.yml`.

## Troubleshooting

- **`Permission denied` on start:** the container runs as uid 1000; the `data` folder must belong to it (`sudo chown 1000:1000 data`), or set `user: "<uid>:<gid>"` to a user that owns the folder.
- **Lost the setup token:** it is in `data/setup-token.txt` and in the log (`docker compose logs dashboard`).
- **Forgot the password:** stop the container, remove the `"auth"` block from `data/config.json`, start it: the wizard opens again with a new setup token, all other settings stay.
