"""Deployment contracts; all outputs use temporary directories outside the checkout."""

import sys

sys.dont_write_bytecode = True
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "release"))
import package

spec = importlib.util.spec_from_file_location(
    "install_package", ROOT / "release/package-install.py"
)
install_package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install_package)
HELM = os.environ.get("HELM", "helm")


def values(port=443, extra=()):
    with tempfile.TemporaryDirectory() as work:
        source = Path(work) / "site.json"
        source.write_text(
            json.dumps(
                {
                    "domain": "lab.example.internal",
                    "issuer": "https://iam.lab.example.internal"
                    + (":" + str(port) if port != 443 else "")
                    + "/realms/master",
                    "httpsPort": port,
                }
            )
        )
        return json.loads(
            subprocess.check_output(
                [
                    sys.executable,
                    str(ROOT / "release/prepare-values.py"),
                    "--site-config",
                    str(source),
                    *extra,
                ],
                text=True,
            )
        )


def render(v):
    with tempfile.TemporaryDirectory() as work:
        source = Path(work) / "values.json"
        source.write_text(json.dumps(v))
        return subprocess.run(
            [
                HELM,
                "template",
                "matrix",
                str(ROOT / "charts/tuwunel"),
                "-n",
                "matrix",
                "-f",
                str(source),
            ],
            capture_output=True,
            text=True,
        )


def docs(v):
    result = render(v)
    if result.returncode:
        raise AssertionError(result.stderr)
    return [d for d in yaml.safe_load_all(result.stdout) if d]


class Deployment(unittest.TestCase):
    def test_cli_device_callback_uses_only_the_homeserver_origin(self):
        v = values(19443)
        route = next(d for d in docs(v) if d["metadata"]["name"] == "matrix-sso-redirect")
        rule = route["spec"]["routes"][0]["match"]
        pattern = re.search(r"QueryRegexp\(`redirectUrl`, `([^`]+)`\)", rule)[1]
        callback = v["homeserverUrl"] + "/_tuwunel/oidc/device_callback?user_code=BCDFG-HJKLM"
        self.assertRegex(callback, pattern)
        self.assertNotRegex(callback + "&next=https://other.example.internal", pattern)
        self.assertNotRegex(callback.replace(v["homeserverUrl"], "https://other.example.internal"), pattern)

    def test_cidr_networkpolicy_preserves_root_context_and_public_port(self):
        for port in [443, 19443]:
            with self.subTest(port=port):
                v = values(port, ["--iam-egress-cidr", "198.51.100.23/32"])
                policy = next(
                    d
                    for d in docs(v)
                    if d["kind"] == "NetworkPolicy"
                    and d["metadata"]["name"] == "matrix-tuwunel-egress"
                )
                rule = policy["spec"]["egress"][-1]
                self.assertEqual(
                    rule["to"], [{"ipBlock": {"cidr": "198.51.100.23/32"}}]
                )
                self.assertEqual(rule["ports"], [{"protocol": "TCP", "port": port}])

    def test_default_topology_probe_and_retained_pvc(self):
        resources = docs(values())
        sts = next(d for d in resources if d["kind"] == "StatefulSet")
        self.assertEqual(sts["metadata"]["name"], "matrix-tuwunel")
        self.assertEqual(
            [c["name"] for c in sts["spec"]["template"]["spec"]["containers"]],
            ["tuwunel", "backup"],
        )
        self.assertEqual(
            sts["spec"]["volumeClaimTemplates"][0]["metadata"]["name"], "data"
        )
        deployments = {
            d["metadata"]["name"]: d for d in resources if d["kind"] == "Deployment"
        }
        self.assertEqual(set(deployments), {"matrix-element", "matrix-alert-receiver"})
        self.assertEqual(
            deployments["matrix-element"]["spec"]["template"]["spec"]["containers"][0][
                "readinessProbe"
            ]["httpGet"],
            {"path": "/config.json", "port": 8080},
        )
        self.assertFalse(any(d["kind"] == "CronJob" for d in resources))
        tls = {
            d["spec"]["tls"][0]["secretName"]
            for d in resources
            if d["kind"] == "Ingress"
        }
        self.assertEqual(
            tls, {"matrix-tuwunel-tls", "matrix-element-tls", "matrix-well-known-tls"}
        )

    def test_chat_media_limits_and_element_theme(self):
        resources = docs(values())
        config = {
            d["metadata"]["name"]: d["data"] for d in resources if d["kind"] == "ConfigMap"
        }
        server = next(d for d in resources if d["kind"] == "StatefulSet")
        homeserver = server["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(homeserver["resources"]["limits"]["memory"], "4Gi")
        toml = next(
            data["tuwunel.toml"] for data in config.values() if "tuwunel.toml" in data
        )
        self.assertIn('max_request_size = "1 GiB"', toml)

        element = next(
            d for d in resources
            if d["kind"] == "Deployment" and d["metadata"]["name"] == "matrix-element"
        )
        pod = element["spec"]["template"]["spec"]
        mount = next(
            m for m in pod["containers"][0]["volumeMounts"]
            if m["mountPath"] == "/app/apeiron-chat"
        )
        self.assertTrue(mount["readOnly"])
        volume = next(v for v in pod["volumes"] if v["name"] == mount["name"])
        assets = config[volume["configMap"]["name"]]
        element_config = json.loads(config["matrix-element-config"]["config.json"])
        policy = {"default": False, "force_disable": True}
        discovery = json.loads(config["matrix-element-config"]["well-known-client.json"])
        self.assertEqual(discovery["io.element.e2ee"], policy)
        self.assertEqual(element_config["default_server_config"]["io.element.e2ee"], policy)
        self.assertIn("allow_encryption = false", toml)
        self.assertEqual(len(element_config["modules"]), 1)
        self.assertRegex(element_config["modules"][0], r"^/apeiron-chat/theme\.js\?v=[a-f0-9]{12}$")
        required_assets = {
            "theme.js", "theme.css", "navigation.js", "components.css", "wordmark-light.svg",
            "beams.css", "login.css", "download.js",
        }
        self.assertTrue(required_assets.issubset(assets))
        self.assertLess(sum(len(content.encode()) for content in assets.values()), 1024**2)

        for key in ["auth_header_logo_url", "welcome_background_url"]:
            asset_url = element_config["branding"][key]
            self.assertTrue(asset_url.startswith("/apeiron-chat/"))
            self.assertIn(asset_url.removeprefix("/apeiron-chat/"), assets)

    def test_all_containers_consume_role_digest_overrides(self):
        v = values()
        roles = yaml.safe_load((ROOT / "charts/tuwunel/values.yaml").read_text())[
            "images"
        ]
        v["global"] = {"imageRegistry": "registry.example.internal"}
        v["images"] = {
            name: {
                "repository": "probe/" + name,
                "tag": "unused",
                "digest": "sha256:" + hashlib.sha256(name.encode()).hexdigest(),
            }
            for name in roles
        }
        found = set()
        for d in docs(v):
            spec = d.get("spec", {}).get("template", {}).get("spec", {})
            found.update(
                c["image"]
                for k in ["containers", "initContainers"]
                for c in spec.get(k, [])
            )
        expected = {
            "registry.example.internal/" + r["repository"] + "@" + r["digest"]
            for r in v["images"].values()
        }
        self.assertEqual(found, expected)

    def test_admin_sync_is_explicit_and_uses_directory_reference(self):
        v = values(19443, ["--iam-egress-cidr", "198.51.100.23/32"])
        v["adminSync"] = {"enabled": True}
        self.assertNotEqual(render(v).returncode, 0)
        v["adminSync"]["directorySecret"] = "matrix-directory"
        resources = docs(v)
        cron = next(d for d in resources if d["kind"] == "CronJob")
        encoded = json.dumps(cron)
        self.assertIn("matrix-directory", encoded)
        self.assertNotIn("keycloak-automation-admin", encoded)
        self.assertNotIn("keycloak-admin", encoded)

    def test_unsafe_configurations_are_rejected(self):
        variants = [
            {"publicUrl": "http://chat.lab.example.internal"},
            {"ingress": {"host": "wrong.example.internal"}},
            {"auth": {"mode": "external"}},
            {"auth": {"oidc": {"callbackUrl": "https://evil.invalid/cb"}}},
            {"ingress": {"httpsPort": 19443}},
            {"e2eAppservice": True},
            {"vasi": {"enabled": True}},
        ]

        def merge(a, b):
            for k, v in b.items():
                if isinstance(v, dict):
                    merge(a.setdefault(k, {}), v)
                else:
                    a[k] = v

        for override in variants:
            with self.subTest(override=override):
                v = values()
                merge(v, override)
                self.assertNotEqual(render(v).returncode, 0)

    def test_prerequisite_is_namespace_scoped_and_explicit(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "rbac.json"
            v = values(
                extra=[
                    "--namespace",
                    "chat",
                    "--release",
                    "chat",
                    "--executor-user",
                    "chentu:vasi-executor:test",
                    "--prerequisites-out",
                    str(path),
                ]
            )
            self.assertEqual(v["auth"]["oidc"]["clientId"], "vasi-chat-chat")
            objects = json.loads(path.read_text())["items"]
            self.assertEqual(
                [d["kind"] for d in objects], ["Namespace", "Role", "RoleBinding"]
            )
            self.assertEqual(objects[1]["metadata"]["namespace"], "chat")
            self.assertEqual(objects[1]["rules"][0]["apiGroups"], ["traefik.io"])
            self.assertEqual(
                set(objects[1]["rules"][0]["resources"]),
                {"ingressroutes", "middlewares"},
            )
            self.assertNotIn("cluster-admin", json.dumps(objects))

    def test_existing_identity_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as work:
            old = values()
            old["auth"]["oidc"]["clientId"] = "foreign-client"
            source = Path(work) / "old.json"
            source.write_text(json.dumps(old))
            with self.assertRaises(subprocess.CalledProcessError):
                values(extra=["--existing-values", str(source)])

    def test_empty_and_external_secrets_do_not_share_legacy_client_default(self):
        v = values()
        self.assertEqual(v["auth"]["oidc"]["clientId"], "vasi-matrix-matrix")
        self.assertEqual(v["auth"]["oidc"]["existingSecret"], "vasi-matrix-oidc")
        self.assertNotIn("client-secret", json.dumps(v))


class Packaging(unittest.TestCase):
    def test_reproducible_chart_has_unique_members_and_resolvable_receipt(self):
        with tempfile.TemporaryDirectory() as work:
            a, b = Path(work) / "a", Path(work) / "b"
            first, second = package.package(a, True), package.package(b, True)
            self.assertEqual(first["chart"]["sha256"], second["chart"]["sha256"])
            artifact = a / first["chart"]["file"]
            self.assertTrue(artifact.is_file())
            with tarfile.open(artifact) as archive:
                names = archive.getnames()
                self.assertEqual(len(names), len(set(names)))
                provenance = json.load(
                    archive.extractfile("tuwunel/files/provenance.json")
                )
                self.assertTrue(provenance["development"])
                self.assertIn("sourceSha256", provenance)
                self.assertIn("tuwunel/files/adaptation-origin.json", names)
            with self.assertRaises(ValueError):
                package.package(a, True)

    def test_formal_build_rejects_dirty_sources(self):
        def git(args, **kwargs):
            return b" M source" if "status" in args else "fixed-commit"

        with (
            tempfile.TemporaryDirectory() as work,
            patch.object(package.subprocess, "check_output", side_effect=git),
        ):
            with self.assertRaisesRegex(ValueError, "clean committed"):
                package.package(Path(work) / "out")

    def test_install_builder_rejects_missing_or_corrupt_images_before_output(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            output = root / "out"
            with self.assertRaisesRegex(ValueError, "unsafe image"):
                install_package.assemble(root, output, True)
            image = root / "images/tuwunel-amd64.oci.tar"
            image.parent.mkdir()
            image.write_bytes(b"corrupt archive")
            with self.assertRaisesRegex(ValueError, "archive bytes differ"):
                install_package.assemble(root, output, True)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
