"""Exercise the installed CLI against disposable Keycloak and Tuwunel containers."""
import argparse
from contextlib import ExitStack
import hashlib
from html.parser import HTMLParser
from http.cookiejar import CookieJar, DefaultCookiePolicy
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
from tempfile import TemporaryDirectory
import time
from urllib import error, parse, request


class Form(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.action = None
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.action = attrs.get("action")
        if tag == "input" and attrs.get("type") == "hidden":
            self.fields[attrs["name"]] = attrs.get("value", "")


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_container(stack, args):
    container = subprocess.check_output(["docker", "run", "-d", *args], text=True).strip()
    stack.callback(subprocess.run, ["docker", "rm", "-fv", container], stdout=subprocess.DEVNULL)
    return container


def ready(url):
    for _ in range(100):
        try:
            with request.urlopen(url, timeout=2) as response:
                return json.load(response)
        except OSError:
            time.sleep(1)
    raise RuntimeError("test service did not become ready")


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").digest()


def exercise(args, stack, root):
    kp, mp = port(), port()
    assert kp != mp
    iam, matrix = (f"http://127.0.0.1:{p}" for p in (kp, mp))
    client_secret, password = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    realm = {
        "realm": "chat-test", "enabled": True, "sslRequired": "none",
        "clients": [{"clientId": "matrix-test", "secret": client_secret,
                     "protocol": "openid-connect", "publicClient": False,
                     "redirectUris": [matrix + "/_matrix/client/unstable/login/sso/callback/matrix-test"]}],
        "users": [{"username": "reader", "enabled": True, "emailVerified": True,
                   "firstName": "Test", "lastName": "Reader", "email": "reader@example.internal",
                   "credentials": [{"type": "password", "value": password}]}],
    }
    realm_file = root / "realm.json"
    realm_file.write_text(json.dumps(realm))
    keycloak = start_container(stack, ["-p", f"127.0.0.1:{kp}:{kp}", "-p", f"127.0.0.1:{mp}:{mp}",
        "-v", f"{realm_file}:/opt/keycloak/data/import/chat-test-realm.json:ro", args.keycloak,
        "start-dev", "--import-realm", f"--http-port={kp}"])
    issuer = iam + "/realms/chat-test"
    ready(issuer + "/.well-known/openid-configuration")
    print("Keycloak ready", flush=True)
    config = root / "tuwunel.toml"
    config.write_text(f'''[global]
server_name = "chat-test.example.internal"
address = ["0.0.0.0"]
port = {mp}
database_path = "/var/lib/tuwunel/db"
allow_federation = false
login_with_password = false
grant_admin_to_first_user = false
cache_capacity_modifier = 0.1
db_pool_max_workers = 4
rocksdb_allow_fallocate = false
allow_encryption = false
oidc_native_auth = false
oidc_registration_allowed_redirect_hosts = ["chat.example.internal"]
access_token_ttl = 60
max_request_size = "1 GiB"
[global.well_known]
client = "{matrix}"
[[global.identity_provider]]
brand = "Keycloak"
client_id = "matrix-test"
client_secret = "{client_secret}"
issuer_url = "{issuer}"
callback_url = "{matrix}/_matrix/client/unstable/login/sso/callback/matrix-test"
userid_claims = ["preferred_username"]
registration = true
default = true
''')
    start_container(stack, ["--memory", "4g", "--network", f"container:{keycloak}", "-v", "/var/lib/tuwunel",
        "-v", f"{config}:/etc/test.toml:ro", args.matrix, "-c", "/etc/test.toml"])
    ready(matrix + "/_matrix/client/v1/auth_metadata")
    print("Tuwunel ready", flush=True)
    session = root / "session.json"
    cmd = [args.bin, "--session", str(session), "--server", matrix]

    def run(*words, stdin=None):
        return json.loads(subprocess.check_output([*cmd, *words], text=True, input=stdin))

    login = subprocess.Popen([*cmd, "login", "--chat-url", "https://chat.example.internal"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stack.callback(login.kill)
    prompt = login.stderr.readline()
    match = re.fullmatch(r"Open (\S+) and approve device code ([A-Z-]+)\n", prompt)
    assert match, "device login did not produce a user code: " + prompt
    # Browsers treat loopback as a trustworthy origin for Secure cookies.
    class LoopbackCookies(DefaultCookiePolicy):
        def return_ok_secure(self, cookie, req):
            return parse.urlsplit(req.full_url).hostname == "127.0.0.1" or super().return_ok_secure(cookie, req)

    browser = request.build_opener(request.HTTPCookieProcessor(CookieJar(LoopbackCookies())))

    def page(url, data=None):
        body = parse.urlencode(data).encode() if data is not None else None
        try:
            response = browser.open(url, body, timeout=20)
        except error.HTTPError as exc:
            # Never report browser URLs or response bodies containing grants.
            raise RuntimeError(f"login step failed: HTTP {exc.code} at {parse.urlsplit(exc.url).path}") from None
        with response:
            return response.geturl(), response.read().decode()

    url, html = page(match[1] + "?" + parse.urlencode({"user_code": match[2]}))
    assert url.startswith(iam + "/"), "expected a Keycloak login form"
    form = Form(html)
    url, html = page(form.action, form.fields | {"username": "reader", "password": password})
    form = Form(html)
    assert form.action == "/_tuwunel/oidc/device_callback", "expected explicit device consent"
    page(parse.urljoin(url, form.action), form.fields | {"action": "approve"})
    stdout, stderr = login.communicate(timeout=30)
    assert login.returncode == 0 and json.loads(stdout)["logged_in"], stderr
    assert session.stat().st_mode & 0o777 == 0o600
    assert run("whoami")["user_id"] == "@reader:chat-test.example.internal"
    creds = json.loads(session.read_text())
    req = request.Request(matrix + "/_matrix/client/v3/createRoom", data=b'{"preset":"private_chat"}',
                          headers={"Authorization": "Bearer " + creds["access_token"], "Content-Type": "application/json"})
    with request.urlopen(req) as response:
        room = json.load(response)["room_id"]
    assert room in run("rooms")["joined_rooms"]
    cursor = run("sync")["next_batch"]
    sent = run("send", room, "--txn-id", "message-1", stdin="agent text")
    assert run("send", room, "--text", "agent text", "--txn-id", "message-1") == sent
    update = run("sync", "--since", cursor, "--timeout-ms", "1000")
    assert any(e["event_id"] == sent["event_id"] for e in update["rooms"][room]["events"])
    # Stream the test data through disk. Increase --mib to exercise the site limit.
    src, dest = root / "attachment test.bin", root / "received"
    with src.open("wb") as file:
        for _ in range(args.mib):
            file.write(os.urandom(1024 * 1024))
    uploaded = run("attachment", "upload", room, str(src), "--txn-id", "file-1")
    event = uploaded["event_id"]
    events = run("read", room, "--limit", "1")
    assert events["chunk"][0]["event_id"] == event
    older = run("read", room, "--from", events["end"])
    assert any(e["event_id"] == sent["event_id"] for e in older["chunk"])
    received = run("attachment", "download", room, event, "--out", str(dest))
    assert received["bytes"] == src.stat().st_size and digest(src) == digest(dest)
    assert subprocess.run([*cmd, "attachment", "download", room, event, "--out", str(dest)],
                          capture_output=True).returncode != 0
    raw = root / "token"
    raw.touch(mode=0o600)
    creds = json.loads(session.read_text())
    raw.write_text(creds["access_token"])
    host_cmd = [args.bin, "--server", matrix, "--token-file", str(raw), "whoami"]
    assert json.loads(subprocess.check_output(host_cmd))["user_id"] == "@reader:chat-test.example.internal"
    raw.chmod(0o644)
    assert subprocess.run(host_cmd, capture_output=True).returncode != 0
    # Force the client to renew now, while the server-side refresh grant is valid.
    creds["expires_at"] = 0
    session.write_text(json.dumps(creds))
    assert run("whoami")["user_id"] == "@reader:chat-test.example.internal"
    current = json.loads(session.read_text())
    assert current["access_token"] != creds["access_token"]
    raw.chmod(0o600)
    raw.write_text(current["access_token"])
    assert run("logout")["logged_out"] and not session.exists()
    revoked = subprocess.run(host_cmd, capture_output=True, text=True)
    assert revoked.returncode != 0 and "401" in revoked.stderr
    print(f"Passed SSO, refresh, permissions, rooms, send, sync, pagination, {args.mib} MiB upload/download and logout.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bin", required=True)
    parser.add_argument("--matrix", default="ghcr.io/matrix-construct/tuwunel:v1.9.3")
    parser.add_argument("--keycloak", default="quay.io/keycloak/keycloak:26.7.4")
    parser.add_argument("--mib", type=int, default=4)
    args = parser.parse_args()
    with TemporaryDirectory() as folder, ExitStack() as stack:
        exercise(args, stack, Path(folder))
