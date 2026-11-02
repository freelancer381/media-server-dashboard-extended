# Installation auf Proxmox VE (LXC)

Der empfohlene Weg: Das Dashboard bekommt einen eigenen, kleinen Debian-Container. Ein Skript auf dem
Proxmox-Host fragt alles Nötige ab, legt den Container an und installiert das Dashboard samt Caddy
(Reverse Proxy, HTTPS) und Update-Dienst.

**Voraussetzungen:** Proxmox VE 8 oder neuer, Zugang als root auf den Host, Internet im neuen Container.

## 1. Skript starten

Auf dem Proxmox-Host (Shell in der Weboberfläche oder per SSH), als root:

```bash
apt install -y git whiptail
git clone https://github.com/lcsfls/media-server-dashboard-extended.git /root/media-server-dashboard
bash /root/media-server-dashboard/proxmox/install-lxc.sh
```

## 2. Die Fragen

| Frage | Bedeutung | Vorschlag |
|---|---|---|
| Container ID | ID des neuen LXC | nächste freie |
| Hostname | Name im Netz | `media-dashboard` |
| **Disk** | Auf welchem Storage der Container liegt. Angeboten werden alle aktiven Storages für Container (`rootdir`), mit freiem Platz | dein SSD-Storage, z. B. `local-zfs` / `local-lvm` |
| Template | Wo die Debian-Vorlage gespeichert wird (nur gefragt, wenn es mehrere gibt) | `local` |
| Disk size / CPU / Memory | Größe des Containers | 4 GB / 1 Kern / 512 MB |
| Network / VLAN | Bridge und optional VLAN-Tag | `vmbr0` |
| IP address | DHCP oder feste IP mit Gateway | feste IP, damit Webhooks sie kennen |
| Proxmox access | Legt den Benutzer `dashboard@pve` mit einem API-Token an, Rolle `PVEAuditor` (nur lesen), und trägt ihn samt Zertifikat des Hosts im Wizard ein | Ja |

Danach erstellt das Skript einen **unprivilegierten** Debian-Container (Nesting an, Autostart an),
installiert alles und zeigt am Ende:

```
 Open:         http://192.168.1.50/setup
 Setup token:  1a2b3c4d-5e6f7a8b
```

## 3. Wizard

`http://<container-ip>/setup` öffnen, Setup-Token eingeben, dann:

1. **Zugang:** Benutzername und Passwort (mindestens 10 Zeichen).
2. **Adresse:** nur IP, oder eigene Domain mit HTTPS:
   - *Let's Encrypt*: Die Domain zeigt öffentlich auf den Container, Port 80 und 443 sind erreichbar.
   - *Interne CA*: Für Namen nur im Heimnetz (`dashboard.home.lan`). Damit der Browser nicht warnt, Caddys Root-Zertifikat importieren: im Container `/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt`.
3. **Proxmox:** Mit dem Token aus dem Skript schon ausgefüllt, „Verbindung testen“, dann Laufwerke und zu überwachende Container auswählen.
4. **Apps:** Haken setzen, URL (aus Sicht des Containers, z. B. `http://192.168.1.21:7878`) und API-Key eintragen, „Testen“.
5. **Startseite**, **Live-Feed** (Webhook-Adressen für die Apps), **Fertig** → Speichern.

## Updates

**Settings → Updates**: „Nach Updates suchen“ zeigt die neueste signierte Version mit Release-Notes.
Mit deinem Passwort bestätigen, dann installiert der Update-Dienst sie. Fortschritt und Protokoll
siehst du live. Optional: automatisch installieren zu einer festen Uhrzeit.

Was dabei passiert: Signatur von `release.json` prüfen (Sigstore, Release-Workflow dieses Repos am Tag),
Release-Policy prüfen, Archiv laden und SHA-256 vergleichen, Einstellungen sichern
(`/var/lib/media-server-dashboard-updater/backups`, die letzten 3), neue Version neben der alten
entpacken, umschalten, neu starten, Gesundheitscheck. Bei einem Fehler läuft wieder die alte Version.

Der Container braucht dafür ausgehend HTTPS zu `api.github.com`, `github.com` und den Download-Hosts
von GitHub (`*.githubusercontent.com`) sowie zu `tuf-repo-cdn.sigstore.dev` (die Vertrauenswurzel, mit der
cosign Signaturen prüft). Blockt deine Firewall das, meldet Settings → Updates „Signatur ungültig“ mit dem Grund.

Von Hand, im Container:

```bash
python3 /usr/local/lib/media-server-dashboard/msd-updater.py check
python3 /usr/local/lib/media-server-dashboard/msd-updater.py status
python3 /usr/local/lib/media-server-dashboard/msd-updater.py install 0.3.0
```

Den Update-Dienst selbst (und die Installationsskripte) aktualisiert ein erneuter Lauf des Installers
vom Host aus. Ein Release, das einen neueren Update-Dienst braucht, sagt das in den Settings:

```bash
cd /root/media-server-dashboard && git pull
bash proxmox/install-lxc.sh --update <CTID>
```

## Den API-Token von Hand anlegen

Falls du beim Skript „Nein“ gewählt hast:

```bash
pveum user add dashboard@pve --comment "Media Server Dashboard (read-only)"
pveum acl modify / --users dashboard@pve --roles PVEAuditor
pveum user token add dashboard@pve msd --privsep 1
pveum acl modify / --tokens 'dashboard@pve!msd' --roles PVEAuditor
```

Im Wizard: URL `https://<host-ip>:8006`, Token-ID `dashboard@pve!msd`, das angezeigte Secret. Für die
Zertifikatsprüfung `/etc/pve/pve-root-ca.pem` vom Host in den Container kopieren
(`pct push <CTID> /etc/pve/pve-root-ca.pem /var/lib/media-server-dashboard/pve-root-ca.pem`) und den Pfad eintragen.

## Was wo liegt (im Container)

| Pfad | Inhalt |
|---|---|
| `/opt/media-server-dashboard/current` | Symlink auf die laufende Version in `releases/` |
| `/var/lib/media-server-dashboard/config.json` | Einstellungen inkl. API-Keys (nur für `msd` lesbar) |
| `/var/lib/media-server-dashboard/{events,game}.json` | Live-Feed und Punkte |
| `/etc/media-server-dashboard/updater.json` | Woher Updates kommen (root) |
| `/etc/caddy/msd/site.caddy` | Vom Wizard geschriebene Caddy-Seite |

Dienste: `media-server-dashboard`, `caddy`, `msd-updater.path`, `msd-updater-check.timer`.
Logs: `journalctl -u media-server-dashboard -u msd-updater -f`

## Entfernen

```bash
pct stop <CTID> && pct destroy <CTID>
pveum user delete dashboard@pve
```
