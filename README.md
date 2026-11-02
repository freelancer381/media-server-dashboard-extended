# Media Server Dashboard · Extended

Ein Dashboard für das Homelab mit Medien-Stack (Radarr, Sonarr, Prowlarr, Bazarr, SABnzbd und weitere)
und ein 3D-Büro, in dem deine Apps die Angestellten sind. Diese Fassung läuft auf **Proxmox (LXC)**,
in **Docker** und auf der **Synology** und beobachtet alles über das Netzwerk.

> **Credits:** Dieses Projekt basiert auf dem
> [Media Server Dashboard](https://github.com/Essq1337/media-server-dashboard) von
> **[Essq1337](https://github.com/Essq1337)**. Von dort stammen die Idee, das Dashboard und das
> komplette 3D-Büro mit seinen Mitarbeitern. Vielen Dank! Das Original ist MIT-lizenziert und für
> einen Mac gebaut; diese Fassung bringt es auf Proxmox, Docker und Synology und erweitert es
> (siehe unten). Der ursprüngliche Copyright-Hinweis steht weiter in [LICENSE](LICENSE).

## Was es kann

- **Dashboard** (`/`): CPU, RAM, Netzwerk und Downloads als Live-Graphen, welche Dienste laufen (Mauszeiger drauf zeigt, was fehlt), SABnzbd-Warteschlange, Speicherplatz, zuletzt hinzugefügte Filme und Serien, was in den nächsten 14 Tagen kommt.
- **Büro** (`/office`): Die Apps sitzen am Schreibtisch, wenn sie arbeiten, und gehen in den Pausenraum, wenn nichts zu tun ist. Eigene Apps kommen als weitere Mitarbeiter dazu.
- **Live-Feed** im Büro: Was die Apps tun, erscheint als Chat und als Sprechblase über dem Kopf des Mitarbeiters („Radarr: hat Dune (2021) in Bluray-2160p importiert“). Quellen sind Webhooks der Apps und das, was das Dashboard selbst bemerkt. Alles kommt live über Server-Sent Events.
- **Team-Tafel (Gamification):** XP, Level und Abzeichen pro Mitarbeiter, Tageszahlen, eine Rangliste und die Tage ohne Fehler. Abschaltbar.
- **Startseite** neben dem Büro (links oder rechts, Kacheln oder Tabelle): eigene Links mit Icons, Online-Check per IP:Port und für verknüpfte Proxmox-Gäste CPU, RAM, Traffic und Uptime.
- **Proxmox:** Host-Werte, Storages und der Status der Container/VMs über die Proxmox-API mit einem Token, der nur lesen darf.
- **Setup-Wizard** (`/setup`, später ⚙ Settings): Benutzer, Adresse (IP oder Domain mit HTTPS), Proxmox, Apps mit Verbindungstest, eigene Apps mit Vorlagen (Lidarr, Readarr, Whisparr, Jellyseerr, Jellyfin, Plex, Tautulli, qBittorrent), Startseite, Live-Feed und Updates.
- **Updates per Klick** (LXC): nur Releases, die der Release-Workflow dieses Repos signiert hat, mit Sicherung, Gesundheitscheck und automatischem Zurückrollen. Docker und Synology aktualisieren über das signierte Image.

Python-Standardbibliothek, kein Build, keine Accounts, keine Analytics.

## Installation

| Wo | Anleitung | Updates |
|---|---|---|
| **Proxmox VE** (eigener LXC, empfohlen) | [docs/proxmox.md](docs/proxmox.md): ein Skript auf dem Host, Laufwerk und Ressourcen per Auswahl | Settings → Updates (ein Klick, signiert) |
| **Docker / Docker Compose** | [docs/docker.md](docs/docker.md) | `docker compose pull && docker compose up -d` |
| **Synology** (Container Manager) | [docs/synology.md](docs/synology.md) | Projekt neu erstellen bzw. Image aktualisieren |

Danach `http://<adresse>/setup` öffnen, den **Setup-Token** eingeben (der Installer zeigt ihn an, bei Docker steht er im Log) und den Wizard durchgehen.

Nur ausprobieren, mit erfundenen Daten:

```bash
python3 server.py --demo
```

Dann http://localhost:8900 öffnen.

## Live-Feed und Webhooks

Ohne weitere Einrichtung meldet das Dashboard, was es beim Abfragen sieht: fertige Downloads, neue Importe,
Suchen, Probleme und Container, die stoppen. Mit Webhooks kommt alles sofort und vollständig an.
Die Adressen samt Anleitung pro App stehen unter **Settings → Live-Feed**, zum Kopieren:

- **Radarr, Sonarr, Prowlarr, Lidarr, Readarr:** Settings → Connect → Webhook, URL `…/hook/<token>/Radarr`
- **Bazarr, SABnzbd:** Benachrichtigung über Apprise mit `json://…/hook/<token>/Bazarr`
- **Jellyfin:** Webhook-Plugin, Generic Destination (Vorlage in den Settings)
- **Eigene Skripte:** `curl -X POST …/hook/<token>/Backup -H 'Content-Type: application/json' -d '{"text": "Backup fertig"}'`

## Updates und Releases

Die Pipeline übernimmt die Ideen von [garnet-ops](https://github.com/lcsfls/garnet-ops):

1. `scripts/release.sh 0.3.0` setzt `VERSION`, macht aus „Unreleased“ im [CHANGELOG](CHANGELOG.md) den Abschnitt des Releases, committet und taggt.
2. `git push origin main v0.3.0` startet [`.github/workflows/release.yml`](.github/workflows/release.yml). Der Workflow lässt die ganze CI laufen (Debian 12 und 13), baut das Archiv und `release.json` mit SHA-256 und der [Release-Policy](.release-policy.json) und **signiert `release.json` keyless per Sigstore** mit seiner eigenen Identität. Dann baut er das Container-Image für amd64 und arm64, testet und signiert es und veröffentlicht erst danach das GitHub-Release.
3. In jeder LXC-Installation prüft der Update-Dienst (root, `updater/msd-updater.py`) täglich und auf Knopfdruck die Releases. Installiert wird nur, wenn die Signatur von **genau diesem Workflow an genau diesem Tag** stammt, die Version neuer ist und die Policy passt. Ablauf: Sicherung der Einstellungen, Entpacken neben der laufenden Version, Umschalten, Neustart, Gesundheitscheck. Antwortet die neue Version nicht, läuft automatisch wieder die alte.

Die App selbst kann nichts installieren: Sie legt nur eine Anfrage ab, die der Update-Dienst prüft. Für die Installation braucht es das Passwort; automatische Installation zu einer festen Uhrzeit lässt sich einschalten.

Ein Image von Hand prüfen:

```bash
cosign verify ghcr.io/lcsfls/media-server-dashboard-extended:0.2.0 \
  --certificate-identity "https://github.com/lcsfls/media-server-dashboard-extended/.github/workflows/release.yml@refs/tags/v0.2.0" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Sicherheit

- Jeder meldet sich an (Benutzer und Passwort, PBKDF2). Vor dem ersten Setup öffnet sich der Wizard nur mit dem Setup-Token.
- API-Keys und der Proxmox-Token liegen nur auf dem Server (`config.json`, Modus 600) und gehen nie an den Browser.
- Für Proxmox reicht die Rolle `PVEAuditor` (nur lesen). Das Dashboard ändert nichts an Host oder Apps.
- Webhooks brauchen den geheimen Token in der URL, sind auf 120 pro Minute begrenzt und dürfen nur JSON schicken. Eine neue Adresse gibt es in den Settings.
- Nach acht Fehlversuchen beim Login wird die echte Client-IP für fünf Minuten gesperrt, auch hinter einem Reverse Proxy (`trusted_proxies` bzw. `MSD_TRUSTED_PROXIES`).
- Nicht ohne HTTPS ins Internet stellen.

## Entwicklung

```bash
python3 -m unittest discover -s tests   # Unit-Tests (Live-Feed, Server, Updater gegen ein nachgebautes GitHub)
python3 tests/smoke.py                  # startet den Server und geht Setup, Login, Webhooks, SSE und Updates durch
python3 server.py --demo                # Demo mit erfundenen Daten und Live-Feed
```

## Lizenz

MIT, siehe [LICENSE](LICENSE). Basiert auf [Essq1337/media-server-dashboard](https://github.com/Essq1337/media-server-dashboard) (MIT).
Das 3D-Büro nutzt [three.js](https://threejs.org) (MIT), mitgeliefert in `vendor/`.
