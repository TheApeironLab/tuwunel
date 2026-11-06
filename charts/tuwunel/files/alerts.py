"""Idempotent namespace-local alert-room initialization. Never logs credentials."""

import json, os, time, urllib.request, urllib.error, urllib.parse
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
base = "http://matrix-tuwunel:8008"
domain = os.environ["MATRIX_DOMAIN"]
token = os.environ["MATRIX_TOKEN"]


def api(path, body=None, allow=()):
    request = urllib.request.Request(
        base + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    try:
        with opener.open(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in allow:
            return None
        raise RuntimeError("Matrix initialization HTTP " + str(error.code)) from None


for _ in range(180):
    try:
        if (
            api("/_matrix/client/v3/account/whoami").get("user_id")
            == "@alertbot:" + domain
        ):
            break
    except (OSError, RuntimeError):
        time.sleep(2)
else:
    raise SystemExit(
        "Alert bot identity unavailable; persistent token must match homeserver"
    )
alias = urllib.parse.quote("#alerts:" + domain, safe="")
room = api("/_matrix/client/v3/directory/room/" + alias, allow=(404,))
if room is None:
    room = api(
        "/_matrix/client/v3/createRoom",
        {
            "room_alias_name": "alerts",
            "name": "平台告警",
            "topic": "Alertmanager 告警（只读，由 @alertbot 发送）",
            "preset": "public_chat",
            "visibility": "private",
            "power_level_content_override": {"events_default": 50},
        },
    )
room_id = room["room_id"]
encoded = urllib.parse.quote(room_id, safe="")
api("/_matrix/client/v3/join/" + encoded, {})
if (
    api(
        "/_matrix/client/v3/rooms/" + encoded + "/state/m.room.encryption/",
        allow=(404,),
    )
    is not None
):
    raise SystemExit("Existing alert room is encrypted; refusing replacement")
config = Path("/input/config.yaml").read_text().replace("${ALERTS_ROOM_ID}", room_id)
# Other substitutions remain the receiver's existing startup behavior (no secrets in ConfigMap).
Path("/runtime/config.yaml").write_text(config)
Path("/runtime/config.yaml").chmod(0o600)
print("Alert room exists, bot membership and unencrypted state verified")
