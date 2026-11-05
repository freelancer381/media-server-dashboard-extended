#!/usr/bin/env python3
"""Starts the dashboard in its container as the user that should own the data.

Started as root (the image's default), it hands /data to PUID:PGID (1000:1000 unless set; Unraid's
template sets 99:100, the owner of /mnt/user/appdata) and drops root before the dashboard starts.
Started as someone else already (`user:` in Compose, as on a Synology), it changes nothing and starts.
Standard library only.
"""
import os
import sys


def ids():
    def one(name, default):
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        if not raw.isdigit():
            sys.exit(f"{name} must be a number, not {raw!r}")
        return int(raw)
    return one("PUID", 1000), one("PGID", 1000)


def hand_over(data, uid, gid):
    """Give the data folder and what is in it to uid:gid. Links are changed themselves, never followed."""
    changed = 0
    for root, dirs, files in os.walk(data):
        for name in [root] + [os.path.join(root, n) for n in dirs + files]:
            st = os.lstat(name)
            if (st.st_uid, st.st_gid) != (uid, gid):
                os.lchown(name, uid, gid)
                changed += 1
    if changed:
        print(f"data folder {data}: handed {changed} item(s) to {uid}:{gid}", flush=True)


def main(argv, exec_=os.execvp):  # execvp: the image's CMD says just "python3", found on PATH
    target = argv[1:] or [sys.executable, "/app/server.py"]
    if os.getuid() == 0:
        uid, gid = ids()
        data = os.environ.get("MSD_DATA") or "/data"
        os.makedirs(data, exist_ok=True)
        hand_over(data, uid, gid)
        if uid != 0:  # PUID=0 keeps root, for whoever really wants it
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            if os.getuid() == 0 or os.geteuid() == 0:
                sys.exit("could not give up root")
        os.environ["HOME"] = data
    exec_(target[0], target)


if __name__ == "__main__":
    main(sys.argv)
