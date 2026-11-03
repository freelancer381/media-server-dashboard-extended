#!/usr/bin/env python3
"""Starts the real server in a temp folder and goes through it over HTTP: setup with the token, the restart,
login, page data, webhooks, the live stream, update requests and the guards around them."""
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    def __init__(self, port):
        self.port = port
        self.cookie = ""

    def req(self, method, path, body=None, headers=None, ctype="application/json"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Cookie": self.cookie, **(headers or {})}
        if body is not None:
            body = json.dumps(body) if ctype == "application/json" and not isinstance(body, (str, bytes)) else body
            h["Content-Type"] = ctype
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        set_cookie = r.getheader("Set-Cookie") or ""
        if set_cookie.startswith("dash_auth="):
            self.cookie = set_cookie.split(";")[0]
        return r.status, r.getheader("Location"), data


def check(cond, what):
    if not cond:
        raise SystemExit(f"FAIL: {what}")
    print(f"ok  {what}")


def wait_health(c, version=None, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            s, _, d = c.req("GET", "/healthz")
            if s == 200 and (version is None or json.loads(d)["version"] == version):
                return json.loads(d)
        except OSError:
            pass
        time.sleep(0.3)
    raise SystemExit("FAIL: server did not come up")


def read_events(port, cookie, want, timeout=8):
    """Read the SSE stream until an event named `want` arrives; returns (events seen, its data)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    s.sendall(f"GET /api/stream HTTP/1.1\r\nHost: x\r\nCookie: {cookie}\r\n\r\n".encode())
    buf, seen, end = b"", [], time.time() + timeout
    while time.time() < end:
        buf += s.recv(65536)
        while b"\n\n" in buf:
            block, buf = buf.split(b"\n\n", 1)
            lines = block.decode().splitlines()
            name = next((l[7:] for l in lines if l.startswith("event: ")), None)
            data = "\n".join(l[6:] for l in lines if l.startswith("data: "))
            if name:
                seen.append(name)
                if name == want:
                    return s, seen, json.loads(data)
    raise SystemExit(f"FAIL: no '{want}' event (saw {seen})")


def main():
    tmp = tempfile.mkdtemp()
    data = os.path.join(tmp, "data")
    os.makedirs(data)
    port = free_port()
    json.dump({"port": port, "bind": "127.0.0.1"}, open(os.path.join(data, "config.json"), "w"))
    # A managed install, as far as the app can tell: the update service's settings and status.
    upd = os.path.join(tmp, "updater.json")
    json.dump({"repo": "acme/dash", "status_file": os.path.join(tmp, "status.json")}, open(upd, "w"))
    json.dump({"state": "idle", "available": {"version": "9.0.0", "installable": True}}, open(os.path.join(tmp, "status.json"), "w"))
    env = {**os.environ, "MSD_DATA": data, "MSD_UPDATER_CONF": upd}
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    c = Client(port)
    try:
        version = open(os.path.join(ROOT, "VERSION")).read().strip()
        check(wait_health(c)["version"] == version, f"/healthz reports {version}")
        s, loc, _ = c.req("GET", "/")
        check(s == 303 and loc == "/setup", "before setup everything leads to /setup")
        s, loc, body = c.req("HEAD", "/")
        check(s == 303 and loc == "/setup" and body == b"", "HEAD works like GET without a body")
        check(c.req("HEAD", "/healthz")[0] == 200, "HEAD /healthz for uptime monitors")
        check(c.req("GET", "/setup/config")[0] == 401, "settings need the setup token")
        # a negative Content-Length is refused at once instead of reading until the connection ends
        raw = socket.create_connection(("127.0.0.1", port), timeout=5)
        raw.sendall(b"POST /login HTTP/1.1\r\nHost: x\r\nContent-Length: -1\r\n\r\n" + b"A" * 65536)
        check(b" 400 " in raw.recv(200), "negative Content-Length gets 400")
        raw.close()
        check(c.req("GET", "/setup")[0] == 200, "the setup page is served")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/setup")
        csp = conn.getresponse().getheader("Content-Security-Policy") or ""
        check("frame-ancestors 'none'" in csp and "connect-src 'self'" in csp, "pages carry a Content-Security-Policy")
        token = open(os.path.join(data, "setup-token.txt")).read().strip()
        check(c.req("POST", "/setup/save", "user=a", ctype="application/x-www-form-urlencoded", headers={"X-Setup-Token": token})[0] == 415, "form posts are refused")
        s, _, d = c.req("POST", "/setup/save", {"user": "admin", "password": "short"}, headers={"X-Setup-Token": token})
        check(s == 400 and "10 characters" in json.loads(d)["error"], "short passwords are refused")
        s, _, d = c.req("POST", "/setup/save", {"title": "Smoke", "user": "admin", "password": "smoke-test-pw", "game": {"enabled": True},
                                                "home": {"enabled": True, "tiles": [{"name": "NAS", "check": "127.0.0.1:1"}]}},
                        headers={"X-Setup-Token": token})
        check(s == 200 and json.loads(d)["ok"], "setup saved")
        time.sleep(1.2)
        wait_health(c)
        check(not os.path.exists(os.path.join(data, "setup-token.txt")), "setup token is gone after setup")
        check(c.req("GET", "/setup/config", headers={"X-Setup-Token": token})[0] == 401, "the old setup token no longer opens the settings")

        s, loc, _ = c.req("POST", "/login", "user=admin&password=wrong", ctype="application/x-www-form-urlencoded")
        check(loc == "/login?e=1" and not c.cookie, "wrong password")
        s, loc, _ = c.req("POST", "/login", "user=Admin&password=smoke-test-pw", ctype="application/x-www-form-urlencoded")
        check(s == 303 and c.cookie, "login (user name case-insensitive)")
        s, _, d = c.req("GET", "/api/data")
        page = json.loads(d)
        check(s == 200 and page["title"] == "Smoke" and page["version"] == version, "page data")
        check(page["home"]["enabled"] and page["home"]["tiles"][0]["name"] == "NAS", "start page in the data")
        check(page["update"]["available"] == "9.0.0", "a newer release shows in the page data")

        cfg = json.load(open(os.path.join(data, "config.json")))
        hook = cfg["webhook_token"]
        check(len(hook) >= 24, "a webhook token was made")
        check(c.req("POST", "/hook/wrong", {"eventType": "Test"})[0] == 404, "webhooks with a wrong token are refused")
        radarr = {"eventType": "Download", "movie": {"title": "Smoke Movie", "year": 2026}, "movieFile": {"quality": "Bluray-1080p"}}
        s, _, d = c.req("POST", f"/hook/{hook}?source=Radarr", radarr, headers={"Cookie": ""})
        check(s == 200 and json.loads(d)["ok"], "Radarr webhook accepted without login")
        check(c.req("POST", f"/hook/{hook}", b"not json")[0] == 400, "broken webhook bodies are refused")

        sock, seen, hello = read_events(port, c.cookie, "hello")
        check(any(e["text"].startswith("imported Smoke Movie") for e in hello["events"]), "the stream starts with the history")
        check(hello["game"]["players"][0]["name"] == "Radarr", "the game counted the import")
        c.req("POST", f"/hook/{hook}?source=SABnzbd", {"version": "1.0", "title": "SABnzbd", "message": "Smoke completed", "type": "success"})
        buf, end, got = b"", time.time() + 6, False
        while time.time() < end and not got:
            buf += sock.recv(65536)
            got = b"Smoke completed" in buf
        sock.close()
        check(got, "a new webhook arrives live on the stream")
        check(c.req("GET", "/api/stream", headers={"Cookie": ""})[0] == 401, "the stream needs a login")

        s, _, d = c.req("GET", "/api/update")
        check(s == 200 and json.loads(d)["managed"], "updates are managed")
        check(c.req("POST", "/api/update/check", {})[0] == 200, "check requested")
        req_file = os.path.join(data, "updater", "request.json")
        check(json.load(open(req_file))["action"] == "check", "the request waits for the update service")
        os.remove(req_file)
        check(c.req("POST", "/api/update/install", {"version": "9.0.0", "password": "nope"})[0] == 403, "install needs the password")
        check(c.req("POST", "/api/update/install", {"version": "8.0.0", "password": "smoke-test-pw"})[0] == 400, "only the checked version")
        check(c.req("POST", "/api/update/install", {"version": "9.0.0", "password": "smoke-test-pw"})[0] == 200, "install requested")
        check(json.load(open(req_file)) ["version"] == "9.0.0", "install request written")
        check(c.req("POST", "/api/update/install", {}, headers={"Cookie": ""})[0] == 401, "update actions need a login")
        check(c.req("POST", "/api/game/reset", {})[0] == 200, "game reset")
        print("smoke test passed")
    finally:
        proc.terminate()
        out = proc.communicate(timeout=5)[0].decode(errors="replace")
        if "Traceback" in out:
            print(out)
            raise SystemExit("FAIL: the server printed a traceback")


if __name__ == "__main__":
    main()
