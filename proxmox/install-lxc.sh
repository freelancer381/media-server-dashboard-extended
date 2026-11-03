#!/usr/bin/env bash
# Creates an LXC for the Media Server Dashboard on this Proxmox VE host and installs it there.
# Run as root on the Proxmox host:
#
#   bash proxmox/install-lxc.sh               new container (asks a few questions)
#   bash proxmox/install-lxc.sh --update 123  put the latest code into container 123 and restart it
#
# Run from a checkout of this repository, the code is copied in from there. Otherwise it is cloned
# from $REPO (branch $BRANCH) inside the container.
set -euo pipefail

REPO="${REPO:-https://github.com/freelancer381/media-server-dashboard-extended.git}"
BRANCH="${BRANCH:-main}"
# Installs and updates come from the newest signed release, checked like the in-app updater does it.
# MSD_FROM_CHECKOUT=1 installs this checkout instead (development), MSD_FROM_GIT=1 the branch above.
GH_REPO="$(echo "$REPO" | sed -E 's#^https://github.com/##; s#\.git$##')"
COSIGN_VERSION=v3.1.3
COSIGN_SHA256_amd64=4629c757b7618056f8ddd7e2625ae9fdd94c0372a65049520bc7d9df9efc7f71
COSIGN_SHA256_arm64=c5d324e091826b0d7a78eb16fef316450b4eb9aaec045611c08ba06f5e73220a
STAGE=/root/msd-src
DATA=/var/lib/media-server-dashboard
PVE_USER="dashboard@pve"
TOKEN_NAME="msd"
SRC="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd || true)"
[ -f "$SRC/server.py" ] || SRC=""
TITLE="Media Server Dashboard"

die() { echo "Error: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "please run as root on the Proxmox host"
command -v pct >/dev/null && command -v pvesm >/dev/null || die "this is not a Proxmox VE host (pct/pvesm missing)"
command -v whiptail >/dev/null || die "whiptail is missing (apt install whiptail)"

ask()   { whiptail --backtitle "$TITLE" --title "$1" --inputbox "$2" 10 70 "$3" 3>&1 1>&2 2>&3; }
yesno() { whiptail --backtitle "$TITLE" --title "$1" --yesno "$2" 12 70; }
menu()  { local t="$1" q="$2"; shift 2; whiptail --backtitle "$TITLE" --title "$t" --menu "$q" 20 76 10 "$@" 3>&1 1>&2 2>&3; }
gb()    { awk -v k="$1" 'BEGIN { printf "%.0f GB", k / 1048576 }'; }

# Puts the code into a staging folder in the container (from this checkout if there is one, otherwise
# with git); install-in-container.sh then installs it as the current release.
# Inside the container: fetch the newest release, check its signature (this repository's release workflow
# at that tag) and the archive's SHA-256, then unpack it into the staging folder.
push_release() {
  local id="$1" boot
  boot=$(mktemp)
  cat > "$boot" <<BOOT
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq --no-install-recommends curl ca-certificates python3 >/dev/null
W=\$(mktemp -d); cd "\$W"
ARCH=\$(dpkg --print-architecture)
case "\$ARCH" in amd64) SUM=$COSIGN_SHA256_amd64 ;; arm64) SUM=$COSIGN_SHA256_arm64 ;; *) echo "unsupported architecture" >&2; exit 1 ;; esac
curl -fsSL -o cosign "https://github.com/sigstore/cosign/releases/download/$COSIGN_VERSION/cosign-linux-\$ARCH"
echo "\$SUM  cosign" | sha256sum -c --status || { echo "cosign checksum mismatch" >&2; exit 1; }
chmod 755 cosign
TAG=\$(curl -fsSL "https://api.github.com/repos/$GH_REPO/releases/latest" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')
[[ "\$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+\$ ]] || { echo "no release found" >&2; exit 1; }
V=\${TAG#v}
B="https://github.com/$GH_REPO/releases/download/\$TAG"
curl -fsSLO "\$B/release.json"; curl -fsSLO "\$B/release.json.sigstore.json"
./cosign verify-blob --bundle release.json.sigstore.json \\
  --certificate-identity "https://github.com/$GH_REPO/.github/workflows/release.yml@refs/tags/\$TAG" \\
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \\
  --certificate-github-workflow-repository "$GH_REPO" --certificate-github-workflow-ref "refs/tags/\$TAG" \\
  --certificate-github-workflow-trigger push release.json >/dev/null
read -r NAME SHA < <(python3 -c 'import json; a=json.load(open("release.json"))["artifact"]; print(a["name"], a["sha256"])')
curl -fsSLo archive.tar.gz "\$B/\$NAME"
echo "\$SHA  archive.tar.gz" | sha256sum -c --status || { echo "archive checksum mismatch" >&2; exit 1; }
rm -rf $STAGE && mkdir -p $STAGE && tar -xzf archive.tar.gz -C $STAGE --strip-components=1 --no-same-owner
echo "    release \$V: signature and checksum OK"
cd / && rm -rf "\$W"
BOOT
  pct push "$id" "$boot" /root/msd-boot.sh
  rm -f "$boot"
  pct exec "$id" -- bash /root/msd-boot.sh
  pct exec "$id" -- rm -f /root/msd-boot.sh
}

push_code() {
  local id="$1"
  pct exec "$id" -- rm -rf "$STAGE"
  if [ "${MSD_FROM_CHECKOUT:-}" != 1 ] && [ "${MSD_FROM_GIT:-}" != 1 ]; then
    echo "==> Newest signed release of $GH_REPO"
    push_release "$id"
  elif [ -n "$SRC" ] && [ "${MSD_FROM_CHECKOUT:-}" = 1 ]; then
    echo "==> Copying code from $SRC"
    local tgz; tgz=$(mktemp --suffix=.tgz)
    tar -C "$SRC" --exclude=.git --exclude=__pycache__ --exclude=config.json --exclude=.secret --exclude=setup-token.txt -czf "$tgz" .
    pct push "$id" "$tgz" /root/msd.tgz
    rm -f "$tgz"
    pct exec "$id" -- bash -c "mkdir -p $STAGE && tar -xzf /root/msd.tgz -C $STAGE && rm /root/msd.tgz"
  else
    echo "==> Cloning $REPO ($BRANCH)"
    pct exec "$id" -- bash -c "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq git >/dev/null && git clone -q --depth 1 -b $BRANCH $REPO $STAGE"
  fi
}

install_staged() {
  pct exec "$1" -- bash "$STAGE/scripts/install-in-container.sh"
  pct exec "$1" -- rm -rf "$STAGE"
}

# ---------- update an existing container ----------
if [ "${1:-}" = "--update" ]; then
  CTID="${2:-}"
  [ -n "$CTID" ] && pct status "$CTID" >/dev/null 2>&1 || die "usage: $0 --update <CTID>"
  push_code "$CTID"
  install_staged "$CTID"
  exit 0
fi

# ---------- questions ----------
whiptail --backtitle "$TITLE" --title "Media Server Dashboard" --msgbox "This creates a small Debian container (LXC) for the dashboard and installs it.\n\nAfterwards you finish the setup in the browser: user, address, Proxmox, apps and start page." 12 70

CTID=$(ask "Container ID" "ID for the new container:" "$(pvesh get /cluster/nextid)") || exit 1
pct status "$CTID" >/dev/null 2>&1 && die "ID $CTID is already taken"
HOSTNAME=$(ask "Hostname" "Hostname of the container:" "media-dashboard") || exit 1

# Where the container's disk goes: every active storage that can hold container volumes.
STORE_MENU=()
while read -r name type _ total _ avail _; do
  STORE_MENU+=("$name" "$(printf '%-10s %s free of %s' "$type" "$(gb "$avail")" "$(gb "$total")")")
done < <(pvesm status -content rootdir 2>/dev/null | awk 'NR > 1 && $3 == "active"')
[ ${#STORE_MENU[@]} -gt 0 ] || die "no active storage for containers (content type 'rootdir') found"
STORAGE=$(menu "Disk" "Install the container on which storage?" "${STORE_MENU[@]}") || exit 1

TMPL_MENU=()
while read -r name type _ _ _ avail _; do
  TMPL_MENU+=("$name" "$(printf '%-10s %s free' "$type" "$(gb "$avail")")")
done < <(pvesm status -content vztmpl 2>/dev/null | awk 'NR > 1 && $3 == "active"')
[ ${#TMPL_MENU[@]} -gt 0 ] || die "no active storage for templates (content type 'vztmpl') found"
if [ ${#TMPL_MENU[@]} -eq 2 ]; then TSTORE="${TMPL_MENU[0]}"; else TSTORE=$(menu "Template" "Keep the Debian template on which storage?" "${TMPL_MENU[@]}") || exit 1; fi

DISK=$(ask "Disk size" "Disk size in GB:" "4") || exit 1
CORES=$(ask "CPU" "CPU cores:" "1") || exit 1
RAM=$(ask "Memory" "Memory in MB:" "512") || exit 1

BR_MENU=()
for b in $(ls /sys/class/net | while read -r i; do [ -d "/sys/class/net/$i/bridge" ] && echo "$i"; done); do
  BR_MENU+=("$b" "$(ip -4 -o addr show dev "$b" 2>/dev/null | awk '{print $4}' | head -1)")
done
[ ${#BR_MENU[@]} -gt 0 ] || die "no network bridge found"
if [ ${#BR_MENU[@]} -eq 2 ]; then BRIDGE="${BR_MENU[0]}"; else BRIDGE=$(menu "Network" "Connect the container to which bridge?" "${BR_MENU[@]}") || exit 1; fi
VLAN=$(ask "VLAN" "VLAN tag (empty for none):" "") || exit 1

if yesno "IP address" "Get the IP address by DHCP?\n\n(Choose No to enter a static address.)"; then
  NET_IP="dhcp"; NET_GW=""
else
  NET_IP=$(ask "Static IP" "IP address with prefix, e.g. 192.168.1.50/24:" "") || exit 1
  [[ "$NET_IP" =~ ^[0-9.]+/[0-9]+$ ]] || die "not an address with prefix: $NET_IP"
  NET_GW=$(ask "Gateway" "Gateway:" "$(ip route | awk '/^default/ {print $3; exit}')") || exit 1
fi

MAKE_TOKEN=no
if yesno "Proxmox access" "Create a read-only API token (user $PVE_USER, role PVEAuditor) so the dashboard can show this host's CPU, memory, storages and guests?\n\nIt is filled in for you in the setup wizard."; then MAKE_TOKEN=yes; fi

CODE="newest signed release of $GH_REPO"
[ "${MSD_FROM_GIT:-}" = 1 ] && CODE="$REPO ($BRANCH), unsigned"
[ "${MSD_FROM_CHECKOUT:-}" = 1 ] && [ -n "$SRC" ] && CODE="$SRC (this checkout), unsigned"
SUMMARY="Container $CTID ($HOSTNAME)\nDisk:    $DISK GB on $STORAGE\nCPU/RAM: $CORES core(s), $RAM MB\nNetwork: $BRIDGE${VLAN:+, VLAN $VLAN}, ${NET_IP}${NET_GW:+ via $NET_GW}\nAPI token: $MAKE_TOKEN\nCode:    $CODE"
yesno "Ready" "$SUMMARY\n\nCreate it now?" || exit 1

# ---------- template ----------
echo "==> Debian template"
pveam update >/dev/null || true
TEMPLATE=$(pveam available --section system | awk '{print $2}' | grep -E '^debian-1[2-9]-standard_.*_amd64\.tar\.(zst|gz|xz)$' | sort -V | tail -1)
[ -n "$TEMPLATE" ] || die "no Debian 12+ template found in 'pveam available'"
pveam list "$TSTORE" | grep -q "$TEMPLATE" || pveam download "$TSTORE" "$TEMPLATE"

# ---------- container ----------
echo "==> Creating container $CTID"
NET="name=eth0,bridge=$BRIDGE,ip=$NET_IP${NET_GW:+,gw=$NET_GW}${VLAN:+,tag=$VLAN}"
pct create "$CTID" "$TSTORE:vztmpl/$TEMPLATE" \
  --hostname "$HOSTNAME" --cores "$CORES" --memory "$RAM" --swap 256 \
  --rootfs "$STORAGE:$DISK" --net0 "$NET" \
  --unprivileged 1 --features nesting=1 --onboot 1 --ostype debian \
  --description "Media Server Dashboard: open http://<ip>/ · update with: bash install-lxc.sh --update $CTID" >/dev/null
pct start "$CTID"

echo "==> Waiting for the network"
for _ in $(seq 1 60); do
  pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
  sleep 1
done
pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 || die "container $CTID has no internet access (needed for apt)"

push_code "$CTID"

# ---------- read-only API token ----------
if [ "$MAKE_TOKEN" = yes ]; then
  echo "==> API token for $PVE_USER"
  pveum user list --output-format json | grep -q "\"$PVE_USER\"" || pveum user add "$PVE_USER" --comment "Media Server Dashboard (read-only)"
  pveum acl modify / --users "$PVE_USER" --roles PVEAuditor
  pveum user token remove "$PVE_USER" "$TOKEN_NAME" >/dev/null 2>&1 || true
  SECRET=$(pveum user token add "$PVE_USER" "$TOKEN_NAME" --privsep 1 --comment "media-server-dashboard CT $CTID" --output-format json \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["value"])')
  pveum acl modify / --tokens "$PVE_USER!$TOKEN_NAME" --roles PVEAuditor
  HOST_IP=$(ip -4 -o addr show dev "$BRIDGE" | awk '{print $4}' | cut -d/ -f1 | head -1)
  [ -n "$HOST_IP" ] || HOST_IP=$(hostname -I | awk '{print $1}')
  # The host's own CA, so the dashboard can check the certificate instead of trusting anything.
  pct exec "$CTID" -- mkdir -p "$DATA"
  pct push "$CTID" /etc/pve/pve-root-ca.pem "$DATA/pve-root-ca.pem"
  PRESEED=$(mktemp)
  python3 - "$PRESEED" "$HOST_IP" "$(hostname)" "$PVE_USER!$TOKEN_NAME" "$SECRET" "$DATA/pve-root-ca.pem" <<'PY'
import json, sys
out, ip, node, tid, secret, ca = sys.argv[1:7]
json.dump({"proxmox": {"url": f"https://{ip}:8006", "node": node, "token_id": tid, "token_secret": secret, "verify_tls": True, "ca_file": ca}}, open(out, "w"))
PY
  pct push "$CTID" "$PRESEED" /root/msd-preseed.json --perms 600
  rm -f "$PRESEED"
fi

echo "==> Installing inside the container"
install_staged "$CTID"

CT_IP=$(pct exec "$CTID" -- hostname -I | awk '{print $1}')
TOKEN=$(pct exec "$CTID" -- cat "$DATA/setup-token.txt" 2>/dev/null || true)
cat <<EOF

────────────────────────────────────────────────────────
 Media Server Dashboard is installed in container $CTID.

 Open:         http://$CT_IP/setup
 Setup token:  ${TOKEN:-(see: pct exec $CTID -- cat $DATA/setup-token.txt)}

 Updates:      in the dashboard under Settings → Updates (signed releases),
               or from this checkout: bash $0 --update $CTID
 Remove:       pct stop $CTID && pct destroy $CTID
               pveum user delete $PVE_USER   (removes the API token)
────────────────────────────────────────────────────────
EOF
