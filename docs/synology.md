# Installing on a Synology (Container Manager)

Written for DSM 7.2 with the **Container Manager** package. The image is available for Intel/AMD
(amd64) and ARM64 models.

## 1. Create the folder

In **File Station**, under `docker` (the shared folder Container Manager creates), create the folder
`media-dashboard` and inside it `data`:

```
/volume1/docker/media-dashboard/data
```

## 2. Permissions for the container

The container writes to `data` as a user with uid 1000. Two ways:

- **Simple (your own user):** over SSH run `id` and note uid/gid (e.g. `uid=1026(alex) gid=100(users)`).
  Put `user: "1026:100"` into the Compose file below. Your user already has access to the folder.
- **Or** give the folder to uid 1000 over SSH: `sudo chown -R 1000:1000 /volume1/docker/media-dashboard/data`

## 3. Create the project

**Container Manager → Project → Create**

- Project name: `media-dashboard`
- Path: `/docker/media-dashboard`
- Source: *Create docker-compose.yml* and paste:

```yaml
services:
  dashboard:
    image: ghcr.io/freelancer381/media-server-dashboard-extended:latest
    container_name: media-server-dashboard
    restart: unless-stopped
    user: "1026:100"            # your uid:gid from step 2 (or drop the line if you ran chown 1000)
    ports:
      - "8900:8900"
    volumes:
      - /volume1/docker/media-dashboard/data:/data
    environment:
      TZ: Europe/Berlin
      # MSD_TRUSTED_PROXIES: "172.20.0.1"   # only after step 6
```

*Next* → (the Web Station portal isn't needed) → *Done*. The container starts.

## 4. Setup token and wizard

**Container Manager → Container → media-server-dashboard → Details → Log** shows

```
Setup not finished: open /setup and enter the setup token: 1a2b3c4d-5e6f7a8b
```

(The token is also in `data/setup-token.txt`.) Open `http://<nas-ip>:8900/setup` and go through the
wizard. Enter the apps with their LAN addresses; if they run on the same Synology, use the NAS IP and
their port (`http://192.168.1.10:7878`).

## 5. Webhooks

**Settings → Live feed** lists the webhook addresses; pick `http://<nas-ip>:8900` as the address so the
apps can reach it. For Bazarr and SABnzbd it turns into a `json://` address for Apprise automatically.

## 6. HTTPS through the Synology reverse proxy (optional)

**Control Panel → Login Portal → Advanced → Reverse Proxy → Create**

- Source: `HTTPS`, hostname `dashboard.example.com`, port `443`
- Destination: `HTTP`, `localhost`, port `8900`
- Certificate: assign it under **Control Panel → Security → Certificate** (Let's Encrypt)

The live feed works behind the Synology proxy without extra headers: the dashboard switches nginx's
buffering off itself (`X-Accel-Buffering: no`).

So that the login lockout hits the real visitor and the cookie is marked `Secure`, enter the address
the proxy reaches the container from: **Container Manager → Network →** `media-dashboard_default` →
*Gateway* (e.g. `172.20.0.1`). Put it into `MSD_TRUSTED_PROXIES` in the Compose file and rebuild the
project.

## Updates

When **Settings → Updates** reports a new version:

**Container Manager → Project → media-dashboard → Action → Stop**, then
**Image → ghcr.io/freelancer381/media-server-dashboard-extended → Update** (or "Update available"),
then **Start** the project again. Or over SSH in the project folder:

```bash
sudo docker compose pull && sudo docker compose up -d
```

Your settings, the feed and the points stay in `data`. Open dashboard pages reconnect by themselves
after the restart and reload once with the new version.

## Troubleshooting

- **Container won't start, "Permission denied":** check step 2 (uid/gid in `user:` or `chown`).
- **"Not reachable" for every app:** the Synology firewall may block connections from Docker networks. Allow `172.16.0.0/12`.
- **Live feed shows "offline":** if another proxy (e.g. Cloudflare) sits in front, it must pass Server-Sent Events through. The dashboard then falls back to polling every 2 seconds and keeps working.
