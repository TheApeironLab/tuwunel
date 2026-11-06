#!/usr/bin/env python3
"""Consume public site context. Never reads or emits passwords, keys or tokens."""

import argparse, json, re, urllib.parse, copy
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--site-config", required=True, type=Path)
p.add_argument("--namespace", default="matrix")
p.add_argument("--release", default="matrix")
p.add_argument("--server-name")
p.add_argument("--existing-values", type=Path)
p.add_argument("--prerequisites-out", type=Path)
p.add_argument("--executor-user")
p.add_argument("--iam-egress-cidr", action="append", default=[])
p.add_argument("--admin-directory-secret")
a = p.parse_args()
c = json.loads(a.site_config.read_text())
policy = c.get("configuration") or c.get("integrationSite") or {}
domain = c.get("domain") or ".".join(
    [policy.get("slug", ""), policy.get("parentDomain", "")]
)
label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
valid = lambda s: (
    isinstance(s, str)
    and len(s) <= 253
    and bool(re.fullmatch(label + r"(?:\." + label + r")+", s))
)
if (
    not valid(domain)
    or not re.fullmatch(label, a.namespace)
    or not re.fullmatch(label, a.release)
):
    p.error("invalid public site domain, namespace or release")
if policy.get("slug") and domain != policy["slug"] + "." + policy["parentDomain"]:
    p.error("site domain differs from public integration policy")
issuer = c.get("issuer") or c.get("iamIssuer")
port = (
    c.get("httpsPort")
    or policy.get("httpsPort")
    or (urllib.parse.urlsplit(issuer).port or 443 if issuer else 443)
)
issuer = (
    issuer
    or "https://iam."
    + domain
    + (":" + str(port) if port != 443 else "")
    + "/realms/master"
)
url = urllib.parse.urlsplit(issuer)
if (
    url.scheme != "https"
    or url.username
    or url.password
    or not url.hostname
    or url.query
    or url.fragment
    or not url.path.startswith("/realms/")
    or (url.port or 443) != port
):
    p.error("inconsistent trusted IAM issuer and public HTTPS port")
if type(port) is not int or not 1 <= port <= 65535:
    p.error("invalid public HTTPS port")
certificate = policy.get(
    "certificateIssuer", c.get("certificateIssuer", {"name": "apeiron-internal-ca"})
)
issuer_name = certificate.get("name") if isinstance(certificate, dict) else certificate
if issuer_name != "apeiron-internal-ca":
    p.error("this platform contract requires apeiron-internal-ca")
name = a.server_name or domain
client = "vasi-" + a.namespace + "-" + a.release
if a.existing_values:
    previous = json.loads(a.existing_values.read_text())
    name = previous["domain"]
    if a.server_name and name != a.server_name:
        p.error("persistent Matrix server_name cannot change")
    oidc = previous["auth"]["oidc"]
    if oidc["issuer"] != issuer or oidc["clientId"] != client:
        p.error("existing Matrix issuer/client identity needs explicit migration")
if not valid(name):
    p.error("invalid Matrix server_name")
import ipaddress

for cidr in a.iam_egress_cidr:
    ipaddress.ip_network(cidr, strict=True)
suffix = ":" + str(port) if port != 443 else ""
chat = "chat." + domain
homeserver = "matrix." + domain
hs = "https://" + homeserver + suffix
v = {
    "domain": name,
    "publicUrl": "https://" + chat + suffix,
    "homeserverUrl": hs,
    "publicDomain": domain + suffix,
    "ingress": {"host": chat, "homeserverHost": homeserver, "httpsPort": port},
    "certificateIssuer": issuer_name,
    "storageClass": c.get("storageClass", "local-path"),
    "rocksdbAllowFallocate": False,
    "caConfigMap": "vasi-" + a.release + "-oidc-ca",
    "imagePullSecrets": [{"name": "vasi-registry-pull"}],
    "auth": {
        "mode": "oidc",
        "oidc": {
            "issuer": issuer,
            "clientId": client,
            "existingSecret": "vasi-" + a.release + "-oidc",
            "callbackUrl": hs + "/_matrix/client/unstable/login/sso/callback/" + client,
        },
    },
    "element": {"embedParentOrigins": ["https://vasi." + domain + suffix]},
    "egressCidrs": a.iam_egress_cidr,
}
if a.existing_values:
    generated = v
    v = copy.deepcopy(previous)
    v.update(generated)
    v["element"] = {**generated["element"], **previous.get("element", {})}
if a.admin_directory_secret:
    v["adminSync"] = {"enabled": True, "directorySecret": a.admin_directory_secret}
if a.prerequisites_out:
    if not a.executor_user or not re.fullmatch(
        r"chentu:vasi-executor:[a-zA-Z0-9_.-]+", a.executor_user
    ):
        p.error("--executor-user must be the installed dedicated executor identity")
    objects = [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": a.namespace}},
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": {"name": "matrix-ingress-deployer", "namespace": a.namespace},
            "rules": [
                {
                    "apiGroups": ["traefik.io"],
                    "resources": ["ingressroutes", "middlewares"],
                    "verbs": [
                        "get",
                        "list",
                        "watch",
                        "create",
                        "update",
                        "patch",
                        "delete",
                    ],
                }
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {"name": "matrix-ingress-deployer", "namespace": a.namespace},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": "matrix-ingress-deployer",
            },
            "subjects": [
                {
                    "kind": "User",
                    "apiGroup": "rbac.authorization.k8s.io",
                    "name": a.executor_user,
                }
            ],
        },
    ]
    a.prerequisites_out.write_text(
        json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}, indent=2)
        + "\n"
    )
print(json.dumps(v, indent=2))
