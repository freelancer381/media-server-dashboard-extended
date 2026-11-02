#!/usr/bin/env python3
#
##############################################################################
### NZBGET POST-PROCESSING SCRIPT                                          ###
#
# Media Server Dashboard: tells the dashboard's live feed when a download finished or failed.
#
# Copy the WebhookUrl from the dashboard (Settings → Live feed → NZBGet).
#
##############################################################################
### OPTIONS                                                                ###
#
# The dashboard's webhook address for NZBGet (http://<dashboard>/hook/<token>/NZBGet).
#WebhookUrl=
#
### NZBGET POST-PROCESSING SCRIPT                                          ###
##############################################################################
"""NZBGet extension (23+ via manifest.json, older versions via the header above). Standard library only."""
import json
import os
import sys
import urllib.request

SUCCESS, ERROR, NONE = 93, 94, 95


def main():
    url = os.environ.get("NZBPO_WEBHOOKURL", "").strip()
    if not url:
        print("[WARNING] WebhookUrl is not set")
        return NONE
    name = os.environ.get("NZBPP_NZBNAME", "a download")
    total = os.environ.get("NZBPP_TOTALSTATUS", "")  # SUCCESS, WARNING, FAILURE, DELETED
    detail = os.environ.get("NZBPP_STATUS", "")
    if total == "SUCCESS":
        event = {"kind": "download", "level": "success", "text": f"finished downloading {name}"}
    elif total == "DELETED":
        event = {"kind": "delete", "level": "info", "text": f"removed {name}"}
    else:
        event = {"kind": "error", "level": "error" if total == "FAILURE" else "warning",
                 "text": f"download {'failed' if total == 'FAILURE' else 'needs attention'}: {name} ({detail.lower()})"}
    body = json.dumps({"source": "NZBGet", **event}).encode()
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except OSError as e:
        print(f"[WARNING] Could not reach the dashboard: {e}")
        return ERROR
    return SUCCESS


if __name__ == "__main__":
    sys.exit(main())
