# Installation auf einer Synology (Container Manager)

Getestet für DSM 7.2 mit dem Paket **Container Manager**. Das Image gibt es für Intel/AMD (amd64) und
ARM64-Modelle.

## 1. Ordner anlegen

In der **File Station** unter `docker` (der Freigabe, die Container Manager anlegt) den Ordner
`media-dashboard` und darin `data` anlegen:

```
/volume1/docker/media-dashboard/data
```

## 2. Rechte für den Container

Der Container schreibt in `data` als Benutzer mit uid 1000. Zwei Möglichkeiten:

- **Einfach (eigener Benutzer):** Per SSH `id` eingeben und uid/gid notieren (z. B. `uid=1026(luis) gid=100(users)`).
  In der Compose-Datei unten dann `user: "1026:100"` eintragen. Dein Benutzer hat ja schon Rechte auf den Ordner.
- **Oder** per SSH dem uid 1000 den Ordner geben: `sudo chown -R 1000:1000 /volume1/docker/media-dashboard/data`

## 3. Projekt anlegen

**Container Manager → Projekt → Erstellen**

- Projektname: `media-dashboard`
- Pfad: `/docker/media-dashboard`
- Quelle: *docker-compose.yml erstellen* und einfügen:

```yaml
services:
  dashboard:
    image: ghcr.io/lcsfls/media-server-dashboard-extended:latest
    container_name: media-server-dashboard
    restart: unless-stopped
    user: "1026:100"            # deine uid:gid aus Schritt 2 (oder die Zeile weglassen, wenn du chown 1000 gemacht hast)
    ports:
      - "8900:8900"
    volumes:
      - /volume1/docker/media-dashboard/data:/data
    environment:
      TZ: Europe/Berlin
      # MSD_TRUSTED_PROXIES: "172.20.0.1"   # erst nach Schritt 6
```

*Weiter* → (Web Station-Portal nicht nötig) → *Fertig*. Der Container startet.

## 4. Setup-Token und Wizard

**Container Manager → Container → media-server-dashboard → Details → Protokoll**: dort steht

```
Setup not finished: open /setup and enter the setup token: 1a2b3c4d-5e6f7a8b
```

(Der Token liegt auch in `data/setup-token.txt`.) Dann `http://<nas-ip>:8900/setup` öffnen und den
Wizard durchgehen. Die Apps trägst du mit ihren LAN-Adressen ein; laufen sie auf derselben Synology,
mit der NAS-IP und ihrem Port (`http://192.168.1.10:7878`).

## 5. Webhooks

Unter **Settings → Live-Feed** stehen die Webhook-Adressen; als Adresse `http://<nas-ip>:8900`
auswählen, damit die Apps sie erreichen. Für Bazarr und SABnzbd wird daraus automatisch eine
`json://`-Adresse für Apprise.

## 6. HTTPS über den Synology-Reverse-Proxy (optional)

**Systemsteuerung → Anmeldeportal → Erweitert → Reverse Proxy → Erstellen**

- Quelle: `HTTPS`, Hostname `dashboard.example.com`, Port `443`
- Ziel: `HTTP`, `localhost`, Port `8900`
- Zertifikat: unter **Systemsteuerung → Sicherheit → Zertifikat** der Domain zuordnen (Let's Encrypt)

Der Live-Feed funktioniert hinter dem Synology-Proxy ohne Zusatz-Header: Das Dashboard schaltet die
Pufferung von nginx selbst ab (`X-Accel-Buffering: no`).

Damit die Login-Sperre den echten Besucher trifft und der Cookie als `Secure` markiert wird, trägst du die
Adresse ein, von der der Proxy beim Container ankommt: **Container Manager → Netzwerk →**
`media-dashboard_default` → *Gateway* (z. B. `172.20.0.1`). Diese Adresse in der Compose-Datei bei
`MSD_TRUSTED_PROXIES` eintragen und das Projekt neu erstellen.

## Updates

Wenn das Dashboard in **Settings → Updates** eine neue Version meldet:

**Container Manager → Projekt → media-dashboard → Aktion → Stoppen**, dann
**Image → ghcr.io/lcsfls/media-server-dashboard-extended → Aktualisieren** (bzw. „Update verfügbar“),
dann das Projekt wieder **Starten**. Oder per SSH im Projektordner:

```bash
sudo docker compose pull && sudo docker compose up -d
```

Deine Einstellungen, der Feed und die Punkte bleiben in `data`.

## Probleme

- **Container startet nicht, „Permission denied“:** Schritt 2 prüfen (uid/gid in `user:` oder `chown`).
- **„Nicht erreichbar“ bei allen Apps:** Die Synology-Firewall erlaubt Verbindungen aus Docker-Netzen evtl. nicht. Regel für `172.16.0.0/12` erlauben.
- **Live-Feed zeigt „offline“:** Läuft ein zusätzlicher Proxy (z. B. Cloudflare) davor, muss er Server-Sent Events durchlassen. Das Dashboard fällt dann auf Abfragen alle 2 Sekunden zurück und funktioniert weiter.
