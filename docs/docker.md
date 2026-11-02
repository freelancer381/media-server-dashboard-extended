# Installation mit Docker

Das Image `ghcr.io/lcsfls/media-server-dashboard-extended` enthält nur Python und das Dashboard
(amd64 und arm64). Jedes Release-Image ist vom Release-Workflow signiert (Sigstore).

## Docker Compose

```bash
mkdir media-dashboard && cd media-dashboard
curl -fsSLO https://raw.githubusercontent.com/lcsfls/media-server-dashboard-extended/main/docker-compose.yml
mkdir data && sudo chown 1000:1000 data
docker compose up -d
docker compose logs dashboard     # zeigt den Setup-Token
```

Dann `http://<host>:8900/setup` öffnen und den Token eingeben.

Die `docker-compose.yml`:

```yaml
services:
  dashboard:
    image: ghcr.io/lcsfls/media-server-dashboard-extended:latest   # oder eine feste Version, z. B. :0.2
    container_name: media-server-dashboard
    restart: unless-stopped
    ports:
      - "8900:8900"
    volumes:
      - ./data:/data
    environment:
      TZ: Europe/Berlin
      # MSD_TRUSTED_PROXIES: "172.18.0.1"   # nur hinter einem Reverse Proxy, siehe unten
```

Ohne Compose:

```bash
docker run -d --name media-server-dashboard --restart unless-stopped \
  -p 8900:8900 -v "$PWD/data:/data" -e TZ=Europe/Berlin \
  ghcr.io/lcsfls/media-server-dashboard-extended:latest
```

## Einstellungen über Umgebungsvariablen

| Variable | Standard | Bedeutung |
|---|---|---|
| `MSD_PORT` | `8900` | Port im Container |
| `MSD_TRUSTED_PROXIES` | leer | Komma-Liste der Reverse-Proxy-Adressen, wie der Container sie sieht |
| `MSD_UPDATE_REPO` | `lcsfls/media-server-dashboard-extended` | Wo nach neuen Releases geschaut wird (nur Hinweis) |
| `TZ` | UTC | Zeitzone für Feed und Tageszahlen |

Alles andere stellst du im Wizard ein; es landet in `/data/config.json`.

## Netzwerk

- Die Apps trägst du im Wizard mit ihrer LAN-Adresse ein (`http://192.168.1.21:7878`). Laufen sie im selben Compose-Projekt, geht auch der Dienstname (`http://radarr:7878`).
- Für **Webhooks** müssen die Apps das Dashboard erreichen: `http://<docker-host>:8900/hook/…`. Die fertigen Adressen stehen unter Settings → Live-Feed.
- Proxmox (optional): URL `https://<proxmox-ip>:8006` und ein Token mit `PVEAuditor` (siehe [proxmox.md](proxmox.md#den-api-token-von-hand-anlegen)). Ohne eigene CA-Datei die Zertifikatsprüfung im Wizard ausschalten oder `pve-root-ca.pem` nach `/data` legen und den Pfad `/data/pve-root-ca.pem` eintragen.

## Hinter einem Reverse Proxy (HTTPS)

Nginx Proxy Manager, Traefik, Caddy oder Synology: einfach auf `http://<host>:8900` weiterleiten.

- **Wichtig für den Live-Feed:** Der Proxy darf Server-Sent Events (`/api/stream`) nicht puffern. Nginx hält sich an den Header `X-Accel-Buffering: no`, den das Dashboard schickt; bei Traefik und Caddy ist nichts zu tun.
- Setze `MSD_TRUSTED_PROXIES` auf die Adresse, von der der Proxy kommt (bei Port-Mapping meist das Gateway des Docker-Netzes, z. B. `172.18.0.1`; `docker network inspect <netz>` zeigt es). Dann sperrt der Login-Schutz den echten Besucher statt des Proxys, und der Login-Cookie wird bei HTTPS als `Secure` markiert.

## Updates

In Settings → Updates zeigt das Dashboard, wenn eine neue Version erschienen ist. Aktualisieren:

```bash
docker compose pull && docker compose up -d
```

Einstellungen, Feed und Punkte bleiben in `./data`. Wer Updates automatisieren will, kann z. B.
Watchtower verwenden; eine feste Version (`:0.2`) bekommt nur Fehlerbehebungen dieser Reihe.

Image-Signatur prüfen:

```bash
cosign verify ghcr.io/lcsfls/media-server-dashboard-extended:0.2.0 \
  --certificate-identity "https://github.com/lcsfls/media-server-dashboard-extended/.github/workflows/release.yml@refs/tags/v0.2.0" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Selbst bauen

```bash
git clone https://github.com/lcsfls/media-server-dashboard-extended.git && cd media-server-dashboard-extended
docker build --build-arg VERSION="$(cat VERSION)" -t media-server-dashboard .
```

In der `docker-compose.yml` dann `image:` durch `build: .` ersetzen.

## Probleme

- **`Permission denied` beim Start:** Der Container läuft als uid 1000; der Ordner `data` muss ihm gehören (`sudo chown 1000:1000 data`) oder du setzt `user: "<uid>:<gid>"` auf einen Benutzer, dem der Ordner gehört.
- **Setup-Token verloren:** steht in `data/setup-token.txt` und im Log (`docker compose logs dashboard`).
- **Passwort vergessen:** Container stoppen, in `data/config.json` den Block `"auth"` löschen, starten: Der Wizard öffnet sich wieder mit neuem Setup-Token, alle anderen Einstellungen bleiben.
