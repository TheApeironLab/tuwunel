"""Application-only Matrix admin reconciliation through an explicit read-only IAM client."""

import os, json, ssl, urllib.request, urllib.parse, urllib.error

issuer = os.environ["IAM_ISSUER"]
ctx = ssl.create_default_context(cafile="/etc/iam-ca/ca.crt")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    NoRedirect(),
    urllib.request.HTTPSHandler(context=ctx),
)


def request(base, path, body=None, token=None, method=None, form=False, allow=()):
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
        if form
        else "application/json"
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    data = (
        None
        if body is None
        else (
            urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        )
    )
    try:
        with opener.open(
            urllib.request.Request(
                base + path, data=data, headers=headers, method=method
            ),
            timeout=20,
        ) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in allow:
            return None
        raise RuntimeError(
            "Scoped admin synchronization HTTP " + str(error.code)
        ) from None
    except Exception:
        raise RuntimeError(
            "Scoped admin synchronization failed; response suppressed"
        ) from None


token = request(
    issuer,
    "/protocol/openid-connect/token",
    {
        "grant_type": "client_credentials",
        "client_id": os.environ["DIRECTORY_CLIENT_ID"],
        "client_secret": os.environ["DIRECTORY_CLIENT_SECRET"],
    },
    form=True,
)["access_token"]
origin, realm = issuer.rsplit("/realms/", 1)
role = urllib.parse.quote(os.environ.get("ADMIN_ROLE", "matrix-admin"), safe="")
holders = []
for offset in range(0, 1000000, 100):
    page = request(
        origin,
        "/admin/realms/"
        + urllib.parse.quote(realm, safe="")
        + "/roles/"
        + role
        + "/users?first="
        + str(offset)
        + "&max=100",
        token=token,
    )
    if not isinstance(page, list):
        raise RuntimeError("Invalid scoped role membership response")
    holders.extend(u["username"] for u in page if u.get("enabled", True))
    if len(page) < 100:
        break
else:
    raise RuntimeError("Role pagination exceeded limit")
base = "http://matrix-tuwunel:8008"
bot = os.environ["MATRIX_ADMIN_TOKEN"]
domain = os.environ["MATRIX_DOMAIN"]


def mx(path, body=None, method=None, allow=()):
    return request(base, path, body, bot, method, allow=allow)


if mx("/_matrix/client/v3/account/whoami").get("user_id") != "@admin-sync:" + domain:
    raise RuntimeError("Admin bot token identity differs")
room = mx(
    "/_matrix/client/v3/directory/room/"
    + urllib.parse.quote("#admins:" + domain, safe="")
)["room_id"]
members = mx(
    "/_matrix/client/v3/rooms/" + urllib.parse.quote(room, safe="") + "/joined_members"
)["joined"]
keep = {"@conduit:" + domain, "@admin-sync:" + domain}
wanted = {"@" + u + ":" + domain for u in holders}
existing = set(members)
# Never create ordinary users. Grant only users already present in the Matrix user directory.
for user in sorted(wanted - existing):
    if (
        mx(
            "/_synapse/admin/v2/users/" + urllib.parse.quote(user, safe=""),
            allow=(404,),
        )
        is None
    ):
        continue
    mx(
        "/_synapse/admin/v2/users/" + urllib.parse.quote(user, safe=""),
        {"admin": True},
        "PUT",
    )
for user in sorted(existing - keep - wanted):
    mx(
        "/_synapse/admin/v2/users/" + urllib.parse.quote(user, safe=""),
        {"admin": False},
        "PUT",
    )
print("Existing Matrix administrators reconciled; no account creation requested")
