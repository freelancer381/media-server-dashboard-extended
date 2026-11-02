#!/usr/bin/env python3
"""Builds a release: the archive of the tagged tree and release.json (signed afterwards by the workflow).

    python3 scripts/build-release.py --version 0.2.0 --out dist [--ref v0.2.0]

release.json says what the update service may install and under which conditions:

    {"schema": 1, "name": "media-server-dashboard", "version": "0.2.0", "tag": "v0.2.0", "commit": "...",
     "created": "...", "artifact": {"name": "...tar.gz", "sha256": "...", "size": 123},
     "policy": {...from .release-policy.json...}, "notes": "...from CHANGELOG.md...", "notesUrl": "..."}
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def notes_for(version):
    """The CHANGELOG.md section of this version, without its heading."""
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        text = f.read()
    m = re.search(rf"^## {re.escape(version)}\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m or not m[1].strip():
        sys.exit(f"CHANGELOG.md has no section '## {version}'")
    return m[1].strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", default="dist")
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "lcsfls/media-server-dashboard-extended"))
    a = ap.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", a.version):
        sys.exit("version must be X.Y.Z")
    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
        if f.read().strip() != a.version:
            sys.exit("VERSION does not match --version")
    with open(os.path.join(ROOT, ".release-policy.json"), encoding="utf-8") as f:
        policy = {k: v for k, v in json.load(f).items() if not k.startswith("_")}

    os.makedirs(a.out, exist_ok=True)
    name = f"media-server-dashboard-{a.version}.tar.gz"
    path = os.path.join(a.out, name)
    prefix = f"media-server-dashboard-{a.version}/"
    # Only what runs on the server; CI, tests and the Proxmox host script stay in the repository.
    paths = [p for p in subprocess.run(["git", "-C", ROOT, "ls-tree", "-r", "--name-only", a.ref], capture_output=True, text=True, check=True).stdout.split()
             if not p.startswith((".github/", "tests/"))]
    with open(path, "wb") as out:
        subprocess.run(["git", "-C", ROOT, "archive", "--format=tar.gz", f"--prefix={prefix}", a.ref, *paths], stdout=out, check=True)
    data = open(path, "rb").read()
    commit = subprocess.run(["git", "-C", ROOT, "rev-parse", a.ref + "^{commit}"], capture_output=True, text=True, check=True).stdout.strip()
    release = {
        "schema": 1, "name": "media-server-dashboard", "version": a.version, "tag": f"v{a.version}", "commit": commit,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artifact": {"name": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)},
        "policy": policy, "notes": notes_for(a.version),
        "notesUrl": f"https://github.com/{a.repo}/blob/v{a.version}/CHANGELOG.md",
    }
    with open(os.path.join(a.out, "release.json"), "w", encoding="utf-8") as f:
        json.dump(release, f, indent=2, ensure_ascii=False)
        f.write("\n")
    with open(os.path.join(a.out, "notes.md"), "w", encoding="utf-8") as f:
        f.write(release["notes"] + "\n")
    print(f"{path} ({len(data)} bytes, sha256 {release['artifact']['sha256']})")


if __name__ == "__main__":
    main()
