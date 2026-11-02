#!/usr/bin/env bash
# Prepares a release on main: sets VERSION, turns "## Unreleased" in CHANGELOG.md into the release's
# section, commits "chore: release X.Y.Z" and tags vX.Y.Z. Pushing the tag starts the release workflow.
#
#   scripts/release.sh 0.3.0
#   git push origin main v0.3.0
set -euo pipefail
cd "$(dirname "$0")/.."
V="${1:-}"
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: $0 X.Y.Z" >&2; exit 1; }
[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "release from main" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "working tree not clean" >&2; exit 1; }
git rev-parse -q --verify "refs/tags/v$V" >/dev/null && { echo "tag v$V exists" >&2; exit 1; }
python3 - "$V" <<'PY'
import re, sys
v = sys.argv[1]
old = open("VERSION").read().strip()
key = lambda s: tuple(int(x) for x in s.split("."))
if key(v) <= key(old):
    sys.exit(f"{v} is not newer than {old}")
text = open("CHANGELOG.md", encoding="utf-8").read()
m = re.search(r"^## Unreleased\n(.*?)(?=^## |\Z)", text, re.M | re.S)
if not m or not m[1].strip():
    sys.exit("CHANGELOG.md: write the changes under '## Unreleased' first")
from datetime import date
text = text[:m.start()] + f"## Unreleased\n\n## {v} - {date.today()}\n\n{m[1].strip()}\n\n" + text[m.end():]
open("CHANGELOG.md", "w", encoding="utf-8").write(text)
open("VERSION", "w").write(v + "\n")
PY
git add VERSION CHANGELOG.md
git commit -q -m "chore: release $V"
git tag -a "v$V" -m "v$V"
echo "Ready. Check .release-policy.json, then: git push origin main v$V"
