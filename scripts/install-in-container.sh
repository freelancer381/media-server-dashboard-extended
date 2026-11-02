#!/usr/bin/env bash
# Installs the dashboard on a Debian/Ubuntu system (normally the LXC that proxmox/install-lxc.sh creates):
# a system user, a systemd service, and Caddy in front of it (HTTP by IP, HTTPS once a domain is set in the wizard).
# Safe to run again: it updates the service files and keeps your settings.
set -euo pipefail

APP="$(cd "$(dirname "$0")/.." && pwd)"
DATA=/var/lib/media-server-dashboard
SITE_DIR=/etc/caddy/msd
SITE="$SITE_DIR/site.caddy"
PRESEED=/root/msd-preseed.json
SVC=media-server-dashboard

[ "$(id -u)" -eq 0 ] || { echo "Please run as root." >&2; exit 1; }
[ -f "$APP/server.py" ] || { echo "server.py not found next to this script ($APP)." >&2; exit 1; }

echo "==> Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3 ca-certificates caddy >/dev/null

echo "==> User and folders"
id msd >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin msd
install -d -o msd -g msd -m 750 "$DATA"
install -d -o msd -g caddy -m 755 "$SITE_DIR"
chown -R root:root "$APP"
chmod -R u=rwX,go=rX "$APP"

echo "==> Settings"
# The base settings for running behind Caddy; anything the wizard or the Proxmox installer set is kept.
python3 - "$DATA/config.json" "$PRESEED" "$SITE" <<'PY'
import json, os, sys
path, preseed, site = sys.argv[1:4]
try:
    with open(path) as f:
        c = json.load(f)
except (OSError, ValueError):
    c = {}
c.update({"port": c.get("port", 8900), "bind": "127.0.0.1", "trusted_proxies": ["127.0.0.1"], "caddy_site_file": site})
if os.path.exists(preseed):
    with open(preseed) as f:
        extra = json.load(f)
    if extra.get("proxmox") and not (c.get("proxmox") or {}).get("token_secret"):
        c["proxmox"] = extra["proxmox"]
    os.remove(preseed)
tmp = path + ".tmp"
with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
    json.dump(c, f, indent=2)
os.replace(tmp, path)
PY
chown msd:msd "$DATA/config.json"
[ -f "$DATA/pve-root-ca.pem" ] && chown msd:msd "$DATA/pve-root-ca.pem" && chmod 644 "$DATA/pve-root-ca.pem"

echo "==> Caddy"
if [ ! -s "$SITE" ]; then
  printf '# Written by the dashboard'"'"'s setup wizard; changes here are overwritten.\nhttp:// {\n    reverse_proxy 127.0.0.1:8900\n}\n' > "$SITE"
fi
chown msd:caddy "$SITE"
chmod 644 "$SITE"
cat > /etc/caddy/Caddyfile <<EOF
# Managed by media-server-dashboard. The site itself is in $SITE (written by the setup wizard).
import $SITE
EOF

echo "==> Services"
cat > /etc/systemd/system/$SVC.service <<EOF
[Unit]
Description=Media Server Dashboard
After=network-online.target
Wants=network-online.target

[Service]
User=msd
Group=msd
Environment=MSD_DATA=$DATA
ExecStart=/usr/bin/python3 $APP/server.py
Restart=always
RestartSec=3
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=$DATA $SITE_DIR

[Install]
WantedBy=multi-user.target
EOF
# Reload Caddy whenever the wizard writes a new site (domain, HTTPS mode).
cat > /etc/systemd/system/msd-caddy-reload.path <<EOF
[Unit]
Description=Reload Caddy when the dashboard changes its site

[Path]
PathChanged=$SITE

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/msd-caddy-reload.service <<EOF
[Unit]
Description=Reload Caddy for the dashboard

[Service]
Type=oneshot
ExecStart=/bin/systemctl reload-or-restart caddy
EOF
systemctl daemon-reload
systemctl enable --now caddy msd-caddy-reload.path >/dev/null 2>&1
systemctl reload-or-restart caddy
systemctl enable $SVC >/dev/null 2>&1
systemctl restart $SVC

for _ in $(seq 1 20); do
  [ -f "$DATA/setup-token.txt" ] || grep -q '"hash"' "$DATA/config.json" 2>/dev/null && break
  sleep 0.5
done
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
if [ -f "$DATA/setup-token.txt" ]; then
  echo "Done. Open http://${IP:-<container-ip>}/setup"
  echo "Setup token: $(cat "$DATA/setup-token.txt")"
else
  echo "Done. The dashboard runs at http://${IP:-<container-ip>}/ (or your domain)."
fi
