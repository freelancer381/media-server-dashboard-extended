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
REPO="${MSD_REPO:-freelancer381/media-server-dashboard-extended}"
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
install -d -o root -g root -m 755 "$SITE_DIR"  # Caddy's site is root's; the app only asks for it (msd-caddy.path)
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
install -m 755 "$TARGET/scripts/msd-caddy.py" "$LIB/msd-caddy.py"
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
c.update({"port": c.get("port", 8900), "bind": "127.0.0.1", "trusted_proxies": ["127.0.0.1"], "caddy_managed": True})
c.pop("caddy_site_file", None)  # the app no longer writes Caddy's site itself
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
# The site comes from the saved settings, through the root helper (also keeps a domain set up before).
MSD_CADDY_NO_RELOAD=1 python3 "$LIB/msd-caddy.py" "$DATA/config.json" "$SITE"
chown root:root "$SITE"
chmod 644 "$SITE"
cat > /etc/caddy/Caddyfile <<EOF
# Managed by media-server-dashboard. The site itself is in $SITE (written by the setup wizard).
import $SITE
EOF

echo "==> systemd"
# Extra sandboxing for the app; if this container's systemd can't apply it, the install falls back below.
HARDENING="ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
PrivateDevices=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
SystemCallArchitectures=native
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0077"
write_app_unit() {
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
ReadWritePaths=$DATA
$1

[Install]
WantedBy=multi-user.target
EOF
}
write_app_unit "$HARDENING"
# When the wizard saves, the root helper writes Caddy's site from the checked settings and reloads Caddy.
systemctl disable --now msd-caddy-reload.path >/dev/null 2>&1 || true
rm -f /etc/systemd/system/msd-caddy-reload.path /etc/systemd/system/msd-caddy-reload.service
cat > /etc/systemd/system/msd-caddy.path <<EOF
[Unit]
Description=Media Server Dashboard: Caddy site requests

[Path]
PathChanged=$DATA/caddy-request
Unit=msd-caddy.service

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/msd-caddy.service <<EOF
[Unit]
Description=Media Server Dashboard: write Caddy's site from the settings

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $LIB/msd-caddy.py $DATA/config.json $SITE
NoNewPrivileges=yes
ProtectHome=yes
PrivateTmp=yes
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
# root, but kept away from what it doesn't need; cosign itself runs as nobody
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictRealtime=yes
LockPersonality=yes
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
systemctl enable --now caddy msd-caddy.path msd-updater.path msd-updater-check.timer >/dev/null 2>&1
systemctl reload-or-restart caddy
systemctl enable $SVC >/dev/null 2>&1
systemctl restart $SVC
# Did the sandboxed app come up? If not, this container can't apply all of it: fall back and say so.
up=""
for _ in $(seq 1 20); do
  if curl -fsS -o /dev/null http://127.0.0.1:8900/healthz; then up=1; break; fi
  sleep 0.5
done
if [ -z "$up" ]; then
  echo "    The extra sandboxing doesn't work in this container; using the basic one." >&2
  write_app_unit ""
  systemctl daemon-reload
  systemctl restart $SVC
fi

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
