# Installing on Unraid

The dashboard runs on Unraid as a Docker container, from a ready-made template: port, app data folder and
user are filled in. Without a Proxmox host it shows the Unraid server itself (CPU, memory, load, disk
activity, how full the array is).

**You need:** Unraid 6.10 or newer with Docker enabled.

## 1. Add the template

In the Unraid web UI open the **Terminal** (top right, `>_`) and run:

```bash
wget -O /boot/config/plugins/dockerMan/templates-user/my-media-server-dashboard.xml https://raw.githubusercontent.com/freelancer381/media-server-dashboard-extended/main/unraid/media-server-dashboard.xml
```

## 2. Create the container

**Docker → Add Container → Template:** pick **media-server-dashboard** under *User templates*. The
fields are filled in:

| Field | Default | Meaning |
|---|---|---|
| WebUI port | `8900` | change the host side if 8900 is taken |
| App data | `/mnt/user/appdata/media-server-dashboard` | settings, API keys, feed, team board, statistics |
| Shares (free space) | `/mnt/user`, read-only | so the dashboard can show how full the array is; remove it if you don't want that |
| PUID / PGID | `99` / `100` | Unraid's `nobody:users`, the owner of appdata. The container hands its app data folder to them and doesn't run as root |
| Trusted proxies | empty | only behind a reverse proxy, see below |
| Host name | empty | the name shown for the server; empty: the Unraid server's name |

**Apply.** Unraid pulls the image and starts it.

## 3. Setup token and wizard

**Docker →** the container's icon **→ Logs** shows the setup token:

```
Setup not finished: open /setup and enter the setup token: 1a2b3c4d-5e6f7a8b
```

The same icon **→ WebUI** opens the dashboard; enter the token and go through the wizard. Enter your apps
with their address as the container reaches them, e.g. `http://192.168.1.10:7878` for Radarr on the same
Unraid server (the server's IP and the app's port).

## Updates

Unraid shows **"update ready"** on the Docker tab when a new release is out (the image's `latest` tag).
**Apply update** pulls it and restarts the container; open pages reconnect by themselves. With the
*Auto Update Applications* plugin this happens on its own. Every image is built and signed by this
repository's release workflow; Settings → Updates says when a new version is out.

## Host numbers

The dashboard reads the server's CPU, memory, load and disk activity from `/proc`, and the free space of
the shares from the `/mnt/user` mapping. Network traffic is the container's own; for the whole server's,
set **Network Type** to `host` (the dashboard is then at `http://<server-ip>:8900`). A Proxmox host entered
in the wizard takes the place of these numbers.

## Behind a reverse proxy

Nginx Proxy Manager, SWAG or Traefik: forward to `http://<server-ip>:8900` (or the container name on a
shared custom network), and put the proxy's address or range into **Trusted proxies** (e.g. `172.16.0.0/12`
for Docker's networks). Then the login lockout sees the real visitor and the login cookie is marked secure.
In the wizard's **Address** step choose **"Behind my own reverse proxy"**.

## Removing it

Docker → the container's icon → **Remove** (tick *also remove image*), then delete
`/mnt/user/appdata/media-server-dashboard` and
`/boot/config/plugins/dockerMan/templates-user/my-media-server-dashboard.xml`.
