"""Pure local initializer safety cases; no real IAM or Matrix endpoints."""

import ast
import unittest
from unittest.mock import patch


def initializer(path, paths=None):
    class RedirectPaths(ast.NodeTransformer):
        def visit_Constant(self, node):
            if isinstance(node.value, str) and node.value in (paths or {}):
                return ast.copy_location(ast.Constant(paths[node.value]), node)
            return node

    return compile(
        ast.fix_missing_locations(RedirectPaths().visit(ast.parse(path.read_text()))),
        str(path),
        "exec",
    )


class Alerts(unittest.TestCase):
    def test_alert_room_creation_reuse_encryption_and_secret_protection(self):
        from pathlib import Path
        import io, json, os, tempfile, urllib.request, urllib.error, contextlib
        from types import SimpleNamespace

        r = Path(__file__).resolve().parents[2]

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.close()

        results = []
        for state in ["new", "existing", "encrypted"]:
            with tempfile.TemporaryDirectory(prefix="matrix-init-test-") as work:
                root = Path(work)
                inp = root / "input"
                out = root / "output"
                inp.write_text(
                    'room: "${ALERTS_ROOM_ID}"\ntoken: "${MATRIX_ACCESS_TOKEN}"\n'
                )
                calls = []

                def request(req, **kwargs):
                    url = req.full_url
                    calls.append((url, req.data))
                    if url.endswith("/account/whoami"):
                        value = {"user_id": "@alertbot:lab.example.internal"}
                    elif "/directory/room/" in url:
                        if state == "new":
                            raise urllib.error.HTTPError(url, 404, "missing", {}, None)
                        value = {"room_id": "!existing:lab.example.internal"}
                    elif url.endswith("/createRoom"):
                        value = {"room_id": "!new:lab.example.internal"}
                    elif "/join/" in url:
                        value = {}
                    elif "/state/m.room.encryption/" in url:
                        if state != "encrypted":
                            raise urllib.error.HTTPError(url, 404, "missing", {}, None)
                        value = {"algorithm": "m.megolm.v1.aes-sha2"}
                    else:
                        raise AssertionError(url)
                    return Response(json.dumps(value).encode())

                source = initializer(
                    r / "charts/tuwunel/files/alerts.py",
                    {"/input/config.yaml": str(inp), "/runtime/config.yaml": str(out)},
                )
                previous = urllib.request.build_opener
                previous_env = dict(os.environ)
                urllib.request.build_opener = lambda *args: SimpleNamespace(
                    open=request
                )
                os.environ.update(
                    MATRIX_DOMAIN="lab.example.internal",
                    MATRIX_TOKEN="synthetic-test-secret",
                    BASIC_PASSWORD="synthetic-password",
                )
                output = io.StringIO()
                try:
                    with contextlib.redirect_stdout(output):
                        exec(
                            source,
                            {"__name__": "__main__"},
                        )
                    assert state != "encrypted"
                    assert out.exists() and out.stat().st_mode & 0o777 == 0o600
                    assert "${MATRIX_ACCESS_TOKEN}" in out.read_text()
                except SystemExit:
                    assert state == "encrypted" and not out.exists()
                finally:
                    urllib.request.build_opener = previous
                    os.environ.clear()
                    os.environ.update(previous_env)
                assert "synthetic-test-secret" not in output.getvalue()
                assert sum(url.endswith("/createRoom") for url, _ in calls) == (
                    1 if state == "new" else 0
                )
                results.append(state)
        # No compilation/execution side effects from compile().
        for f in (r / "charts/tuwunel/files").glob("*.py"):
            compile(f.read_text(), str(f), "exec")


class Directory(unittest.TestCase):
    def test_admin_grants_revocation_and_iam_denied_no_write(self):
        from pathlib import Path
        import os, json, io, urllib.request, urllib.error, contextlib
        from types import SimpleNamespace

        r = Path(__file__).resolve().parents[2]

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        results = []
        for fail in [False, True]:
            writes = []
            calls = []

            def request(req, **kwargs):
                u = req.full_url
                calls.append(u)
                if u.endswith("/protocol/openid-connect/token"):
                    v = {"access_token": "synthetic-iam-token"}
                elif "/roles/matrix-admin/users?" in u:
                    if fail:
                        raise urllib.error.HTTPError(u, 403, "denied", {}, None)
                    v = [
                        {"username": name, "enabled": True}
                        for name in ["retained", "newexisting", "notyet"]
                    ]
                elif u.endswith("/account/whoami"):
                    v = {"user_id": "@admin-sync:lab.example.internal"}
                elif "/directory/room/" in u:
                    v = {"room_id": "!admins:lab.example.internal"}
                elif u.endswith("/joined_members"):
                    v = {
                        "joined": {
                            k: {}
                            for k in [
                                "@conduit:lab.example.internal",
                                "@admin-sync:lab.example.internal",
                                "@retained:lab.example.internal",
                                "@old:lab.example.internal",
                            ]
                        }
                    }
                elif "/_synapse/admin/v2/users/" in u:
                    if req.get_method() == "PUT":
                        writes.append((u, json.loads(req.data)))
                        v = {}
                    elif "notyet" in u:
                        raise urllib.error.HTTPError(u, 404, "absent", {}, None)
                    else:
                        v = {"name": "@newexisting:lab.example.internal"}
                else:
                    raise AssertionError(u)
                return Response(json.dumps(v).encode())

            old = urllib.request.build_opener
            previous = dict(os.environ)
            urllib.request.build_opener = lambda *args: SimpleNamespace(open=request)
            os.environ.update(
                IAM_ISSUER="https://iam.lab.example.internal/realms/master",
                DIRECTORY_CLIENT_ID="matrix-directory",
                DIRECTORY_CLIENT_SECRET="synthetic-client-secret",
                MATRIX_ADMIN_TOKEN="synthetic-admin-token",
                MATRIX_DOMAIN="lab.example.internal",
            )
            source = initializer(r / "charts/tuwunel/files/admin-sync.py")
            out = io.StringIO()
            try:
                with (
                    contextlib.redirect_stdout(out),
                    patch("ssl.create_default_context", return_value=None),
                ):
                    exec(
                        source,
                        {"__name__": "__main__"},
                    )
                assert not fail
            except RuntimeError:
                assert fail
            finally:
                urllib.request.build_opener = old
                os.environ.clear()
                os.environ.update(previous)
            if fail:
                assert not writes
            else:
                assert len(writes) == 2
                assert any(
                    "newexisting" in url and body == {"admin": True}
                    for url, body in writes
                )
                assert any(
                    "old" in url and body == {"admin": False} for url, body in writes
                )
                assert not any("notyet" in url for url, _ in writes)
            assert (
                "synthetic-client-secret" not in out.getvalue()
                and "synthetic-admin-token" not in out.getvalue()
            )
            results.append(fail)
