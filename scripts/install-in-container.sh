#!/usr/bin/env bash
# Installs the dashboard on a Debian/Ubuntu system (normally the LXC that proxmox/install-lxc.sh creates):
# a system user, the app as a systemd service, Caddy in front of it (HTTP by IP, HTTPS once a domain is
# set in the wizard) and the update service (root, installs only signed releases).
# Safe to run again: it installs the code next to it as the current release and keeps your settings.
#
#   MSD_REPO=owner/name   the GitHub repository updates come from (default below)
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP_ROOT=/opt/media-server-dashboard
DATA=/var/lib/media-server-dashboard
LIB=/usr/local/lib/media-server-dashboard
ETC=/etc/media-server-dashboard
UPD_WORK=/var/lib/media-server-dashboard-updater
SITE_DIR=/etc/caddy/msd
SITE="$SITE_DIR/site.caddy"
PRESEED=/root/msd-preseed.json
SVC=media-server-dashboard
REPO="${MSD_REPO:-lcsfls/media-server-dashboard-extended}"
# cosign checks release signatures; the same version as the release workflow, pinned by checksum.
COSIGN_VERSION=v3.1.3
COSIGN_SHA256_amd64=4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71
COSIGN_SHA256_arm64=c5d324e091826b0d7a78eb16fef316450b4eb9aaec045611c08ba06f5e73220a

[ "$(id -u)" -eq 0 ] || { echo "Please run as root." >&2; exit 1; }
[ -f "$SRC/server.py" ] && [ -f "$SRC/VERSION" ] || { echo "server.py/VERSION not found in $SRC." >&2; exit 1; }
VERSION="$(tr -d '[:space:]' < "$SRC/VERSION")"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "bad VERSION: $VERSION" >&2; exit 1; }

echo "==> Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3 ca-certificates caddy curl >/dev/null

echo "==> cosign $COSIGN_VERSION"
ARCH="$(dpkg --print-architecture)"
case "$ARCH" in amd64) SUM="$COSIGN_SHA256_amd64" ;; arm64) SUM="$COSIGN_SHA256_arm64" ;; *) echo "unsupported architecture $ARCH" >&2; exit 1 ;; esac
install -d -m 755 "$LIB"
if ! echo "$SUM  $LIB/cosign" | sha256sum -c --status 2>/dev/null; then
  curl -fsSL -o "$LIB/cosign.tmp" "https://github.com/sigstore/cosign/releases/download/$COSIGN_VERSION/cosign-linux-$ARCH"
  echo "$SUM  $LIB/cosign.tmp" | sha256sum -c --status || { echo "cosign checksum mismatch" >&2; rm -f "$LIB/cosign.tmp"; exit 1; }
  chmod 755 "$LIB/cosign.tmp" && mv "$LIB/cosign.tmp" "$LIB/cosign"
fi

echo "==> User and folders"
id msd >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin msd
install -d -o msd -g msd -m 750 "$DATA" "$DATA/updater"
install -d -o msd -g caddy -m 755 "$SITE_DIR"
install -d -m 711 "$UPD_WORK"
install -d -m 755 "$UPD_WORK/public"
install -d -m 755 "$ETC"

echo "==> App $VERSION"
# An install from before releases existed had the code directly in $APP_ROOT: keep it aside.
if [ -f "$APP_ROOT/server.py" ] && [ ! -L "$APP_ROOT/current" ]; then
  mv "$APP_ROOT" "$APP_ROOT.old-$(date +%Y%m%d%H%M%S)"
fi
install -d -m 755 "$APP_ROOT/releases"
TARGET="$APP_ROOT/releases/$VERSION"
if [ "$(realpath "$SRC")" != "$(realpath -m "$TARGET")" ]; then
  rm -rf "$TARGET.tmp"
  mkdir -p "$TARGET.tmp"
  tar -C "$SRC" --exclude=.git --exclude=__pycache__ --exclude=config.json --exclude=.secret --exclude=setup-token.txt -cf - . | tar -C "$TARGET.tmp" -xf -
  chown -R root:root "$TARGET.tmp"
  chmod -R u=rwX,go=rX "$TARGET.tmp"
  rm -rf "$TARGET"
  mv "$TARGET.tmp" "$TARGET"
fi
ln -sfn "$TARGET" "$APP_ROOT/current.new" && mv -T "$APP_ROOT/current.new" "$APP_ROOT/current"

echo "==> Update service"
install -m 755 "$TARGET/updater/msd-updater.py" "$LIB/msd-updater.py"
python3 - "$ETC/updater.json" "$REPO" "$APP_ROOT" "$DATA" "$UPD_WORK" "$LIB" <<'PY'
import json, os, sys
path, repo, app_root, data, work, lib = sys.argv[1:7]
try:
    c = json.load(open(path))
except (OSError, ValueError):
    c = {}
c.setdefault("repo", repo)
c.setdefault("channel", "stable")
c.update({"workflow": ".github/workflows/release.yml", "app_root": app_root, "data_dir": data, "work_dir": work,
          "status_file": f"{work}/public/status.json", "service": "media-server-dashboard",
          "health_url": "http://127.0.0.1:8900/healthz", "cosign": f"{lib}/cosign"})
tmp = path + ".tmp"
json.dump(c, open(tmp, "w"), indent=2)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY

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
if [ -f "$DATA/pve-root-ca.pem" ]; then chown msd:msd "$DATA/pve-root-ca.pem"; chmod 644 "$DATA/pve-root-ca.pem"; fi

echo "==> Caddy"
if [ ! -s "$SITE" ]; then
  printf '%s\nhttp:// {\n    reverse_proxy 127.0.0.1:8900\n}\n' "# Written by the dashboard's setup wizard; changes here are overwritten." > "$SITE"
fi
chown msd:caddy "$SITE"
chmod 644 "$SITE"
cat > /etc/caddy/Caddyfile <<EOF
# Managed by media-server-dashboard. The site itself is in $SITE (written by the setup wizard).
import $SITE
EOF

echo "==> systemd"
cat > /etc/systemd/system/$SVC.service <<EOF
[Unit]
Description=Media Server Dashboard
After=network-online.target
Wants=network-online.target

[Service]
User=msd
Group=msd
Environment=MSD_DATA=$DATA
ExecStart=/usr/bin/python3 $APP_ROOT/current/server.py
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
# The update service: started when the app leaves a request, and once a day to look for releases.
cat > /etc/systemd/system/msd-updater.path <<EOF
[Unit]
Description=Media Server Dashboard update requests

[Path]
PathExists=$DATA/updater/request.json
Unit=msd-updater.service

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/msd-updater.service <<EOF
[Unit]
Description=Media Server Dashboard update service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $LIB/msd-updater.py run-request
TimeoutStartSec=15min
EOF
cat > /etc/systemd/system/msd-updater-check.service <<EOF
[Unit]
Description=Look for Media Server Dashboard releases

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $LIB/msd-updater.py check timer
EOF
cat > /etc/systemd/system/msd-updater-check.timer <<EOF
[Unit]
Description=Look for Media Server Dashboard releases daily

[Timer]
OnCalendar=daily
RandomizedDelaySec=3h
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now caddy msd-caddy-reload.path msd-updater.path msd-updater-check.timer >/dev/null 2>&1
systemctl reload-or-restart caddy
systemctl enable $SVC >/dev/null 2>&1
systemctl restart $SVC

for _ in $(seq 1 20); do
  if [ -f "$DATA/setup-token.txt" ] || grep -q '"hash"' "$DATA/config.json" 2>/dev/null; then break; fi
  sleep 0.5
done
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
if [ -f "$DATA/setup-token.txt" ]; then
  echo "Done. Open http://${IP:-<container-ip>}/setup"
  echo "Setup token: $(cat "$DATA/setup-token.txt")"
else
  echo "Done ($VERSION). The dashboard runs at http://${IP:-<container-ip>}/ (or your domain)."
fi
