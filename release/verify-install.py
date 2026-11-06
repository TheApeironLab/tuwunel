#!/usr/bin/env python3
"""Explicit read-only acceptance. Real login/messages/restore remain separate tests."""

import argparse, json, subprocess, ssl, urllib.request
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--values", type=Path, required=True)
p.add_argument("--kubeconfig", type=Path, required=True)
p.add_argument("--ca-file", type=Path, required=True)
p.add_argument("--namespace", default="matrix")
a = p.parse_args()
v = json.loads(a.values.read_text())


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def kube(*args):
    r = subprocess.run(
        [
            "kubectl",
            "--kubeconfig",
            str(a.kubeconfig),
            "-n",
            a.namespace,
            *args,
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode:
        raise RuntimeError("Kubernetes read failed; check explicit cluster/namespace")
    return json.loads(r.stdout)


identity = kube("get", "configmap", "matrix-server-name")["data"]
expected = {
    "server_name": v["domain"],
    "client_id": v["auth"]["oidc"]["clientId"],
    "issuer_url": v["auth"]["oidc"]["issuer"],
}
if identity != expected:
    raise RuntimeError("Installed Matrix identity differs")
for kind, name in [
    ("statefulset", "matrix-tuwunel"),
    ("deployment", "matrix-element"),
] + (
    [("deployment", "matrix-alert-receiver")]
    if v.get("alerts", {}).get("enabled", True)
    else []
):
    d = kube("get", kind, name)
    n = d["spec"].get("replicas", 1)
    s = d.get("status", {})
    if (
        n < 1
        or s.get("observedGeneration", 0) < d["metadata"].get("generation", 1)
        or s.get("readyReplicas", 0) != n
    ):
        raise RuntimeError("Workload not ready: " + name)
certs = kube("get", "certificates")["items"]
required = {"matrix-tuwunel-tls", "matrix-element-tls", "matrix-well-known-tls"}
ready = {
    c["spec"]["secretName"]
    for c in certs
    if any(
        x.get("type") == "Ready" and x.get("status") == "True"
        for x in c.get("status", {}).get("conditions", [])
    )
}
if not required <= ready:
    raise RuntimeError("Application TLS certificates not Ready")
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(
        context=ssl.create_default_context(cafile=str(a.ca_file))
    ),
)


def get(url):
    with opener.open(url, timeout=30) as r:
        if r.status != 200:
            raise RuntimeError("HTTPS acceptance failed")
        return json.load(r)


config = get(v["publicUrl"] + "/config.json")
hs = config["default_server_config"]["m.homeserver"]
check(
    hs["base_url"].rstrip("/") == v["homeserverUrl"].rstrip("/")
    and hs["server_name"] == v["domain"],
    "Element homeserver configuration differs",
)
flows = get(v["homeserverUrl"] + "/_matrix/client/v3/login")["flows"]
check(
    "m.login.password" not in {f["type"] for f in flows},
    "Password login unexpectedly enabled",
)
providers = [
    x["id"]
    for f in flows
    if f["type"] == "m.login.sso"
    for x in f.get("identity_providers", [])
]
check(providers == [v["auth"]["oidc"]["clientId"]], "SSO provider differs")
metadata = get(v["homeserverUrl"] + "/_matrix/client/v1/auth_metadata")
check(
    metadata["issuer"].rstrip("/") == v["homeserverUrl"].rstrip("/"),
    "Matrix discovery issuer differs",
)
issuer = v["auth"]["oidc"]["issuer"]
check(
    get(issuer + "/.well-known/openid-configuration")["issuer"] == issuer,
    "IAM discovery issuer differs",
)
port = ":" + str(v["ingress"]["httpsPort"]) if v["ingress"]["httpsPort"] != 443 else ""
discovery = get("https://" + v["domain"] + port + "/.well-known/matrix/client")
check(
    discovery["io.element.e2ee"]["force_disable"] is True,
    "Encryption discovery policy differs",
)
pvc = kube("get", "pvc", "data-matrix-tuwunel-0")
check(pvc["status"]["phase"] == "Bound", "Data PVC is not Bound")
print(
    json.dumps(
        {
            "readOnlyAcceptance": "passed",
            "namespace": a.namespace,
            "pvcUid": pvc["metadata"]["uid"],
            "certificateReady": True,
            "httpsDiscovery": True,
            "ssoOnlyFlows": True,
            "realLoginMessagesAndRestore": "not-run",
        },
        indent=2,
    )
)
