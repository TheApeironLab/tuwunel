"""Exercise Matrix media on a disposable homeserver in a 4 GiB Docker container.

Usage: python3 tests/media/streaming.py IMAGE
The image must start tuwunel as its entrypoint. No existing site is contacted.
"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

MIB = 1024**2
LIMIT = 1024 * MIB
UPLOAD = "/_matrix/media/v3/upload"
DOWNLOAD = "/_matrix/client/v1/media/download/"
CONFIG = """
[global]
server_name = "example.internal"
database_path = "/var/lib/tuwunel/db"
address = ["0.0.0.0"]
port = 8008
max_request_size = "1 GiB"
allow_federation = false
allow_registration = false
allow_legacy_media = false
trusted_servers = []
db_pool_max_workers = 8
cache_capacity_modifier = 0.5
[global.appservice.media_test]
sender_localpart = "tester"
[[global.appservice.media_test.users]]
exclusive = true
regex = "^@tester:example[.]internal$"
"""


def docker(*args, **kwargs):
    return subprocess.check_output(["docker", *args], text=True, **kwargs).strip()


def run(image):
    env = os.environ.copy()
    token = secrets.token_hex(32)
    env["TUWUNEL_APPSERVICE__MEDIA_TEST__AS_TOKEN"] = token
    env["TUWUNEL_APPSERVICE__MEDIA_TEST__HS_TOKEN"] = secrets.token_hex(32)
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "config.toml"
        config.write_text(CONFIG)
        container = docker("run", "-d", "--memory", "4g", "--cpus", "2",
                           "-p", "127.0.0.1::8008", "-v", "/var/lib/tuwunel",
                           "-v", f"{config}:/etc/media-test.toml:ro",
                           "-e", "TUWUNEL_CONFIG=/etc/media-test.toml",
                           "-e", "TUWUNEL_APPSERVICE__MEDIA_TEST__AS_TOKEN",
                           "-e", "TUWUNEL_APPSERVICE__MEDIA_TEST__HS_TOKEN", image, env=env)
        try:
            exercise(container, token)
            assert docker("inspect", "-f", "{{.State.OOMKilled}}", container) == "false"
        finally:
            docker("rm", "-fv", container)


def exercise(container, token):
    port = int(docker("port", container, "8008/tcp").rsplit(":", 1)[1])
    block = os.urandom(MIB)
    auth = {"Authorization": "Bearer " + token}

    def chunks(size):
        for _ in range(size // len(block)):
            yield block
        if size % len(block):
            yield block[:size % len(block)]

    def request(method, path, size=None, *, authorize=True, chunked=False):
        headers = auth.copy() if authorize else {}
        payload = None
        if size is not None:
            headers["Content-Type"] = "application/octet-stream"
            if not chunked:
                headers["Content-Length"] = str(size)
            payload = chunks(size)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=240)
        try:
            conn.request(method, path, payload, headers, encode_chunked=chunked)
            res = conn.getresponse()
            if res.status == 200 and path.startswith(DOWNLOAD):
                h = hashlib.sha256()
                count = 0
                while data := res.read(MIB):
                    h.update(data)
                    count += len(data)
                assert int(res.getheader("Content-Length")) == count
                return res.status, (count, h.digest())
            return res.status, json.loads(res.read())
        finally:
            conn.close()

    for _ in range(150):
        try:
            if request("GET", "/_matrix/client/versions")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(0.2)
    assert request("GET", "/_matrix/client/v1/media/config") == (200, {"m.upload.size": LIMIT})
    assert request("POST", UPLOAD, 128, authorize=False)[0] == 401

    code, empty = request("POST", UPLOAD, 0)
    assert code == 200
    empty_mxc = urlsplit(empty["content_uri"])
    assert request("GET", DOWNLOAD + empty_mxc.netloc + empty_mxc.path) == (
        200, (0, hashlib.sha256(b"").digest()))

    # Keep a record of anonymous memory, separate from the kernel's filesystem cache.
    phase = "upload"
    peaks = {}
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            stats = docker("exec", container, "cat", "/sys/fs/cgroup/memory.stat")
            anon = int(dict(line.split() for line in stats.splitlines())["anon"])
            peaks[phase] = max(peaks.get(phase, 0), anon)
            stop.wait(0.25)

    sampler = threading.Thread(target=sample)
    sampler.start()
    try:
        expected = hashlib.sha256()
        for chunk in chunks(LIMIT):
            expected.update(chunk)
        code, data = request("POST", UPLOAD + "?filename=boundary.bin", LIMIT)
        assert code == 200, (code, data)
        mxc = urlsplit(data["content_uri"])
        phase = "download"
        code, downloaded = request("GET", DOWNLOAD + mxc.netloc + mxc.path + "/boundary.bin")
        assert (code, downloaded) == (200, (LIMIT, expected.digest()))
        print("1 GiB upload, named download and SHA-256 passed", flush=True)

        phase = "concurrent downloads"
        with ThreadPoolExecutor(max_workers=2) as workers:
            downloads = [workers.submit(request, "GET", DOWNLOAD + mxc.netloc + mxc.path)
                         for _ in range(2)]
            for transfer in downloads:
                assert transfer.result() == (200, (LIMIT, expected.digest()))
        print("Two concurrent 1 GiB downloads matched SHA-256", flush=True)

        phase = "oversize"
        code, data = request("POST", UPLOAD, LIMIT + 1, chunked=True)
        assert (code, data["errcode"]) == (413, "M_TOO_LARGE")
        print("Chunked 1 GiB + 1 byte rejected", flush=True)

        phase = "interruption"
        code, data = request("POST", "/_matrix/media/v1/create", 0)
        assert code == 200, (code, data)
        mxc = urlsplit(data["content_uri"])
        path = UPLOAD + "/" + mxc.netloc + mxc.path
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.putrequest("PUT", path)
        conn.putheader("Authorization", auth["Authorization"])
        conn.putheader("Content-Length", str(LIMIT))
        conn.endheaders()
        conn.send(block)
        conn.close()
        assert request("PUT", path, MIB)[0] == 200
        code, data = request("GET", DOWNLOAD + mxc.netloc + mxc.path)
        assert (code, data) == (200, (MIB, hashlib.sha256(block).digest()))
        code, data = request("PUT", path, 1)
        assert data["errcode"] == "M_CANNOT_OVERWRITE_MEDIA"
        files = docker("exec", container, "ls", "-A", "/var/lib/tuwunel/db/media").splitlines()
        assert len(files) == 3, files
        handles = docker("exec", container, "ls", "-l", "/proc/1/fd")
        assert not any("/db/" in line and "(deleted)" in line
                       for line in handles.splitlines()), "upload staging handle still open"
        print("Interrupted PUT, retry, immutable MXC and cleanup passed", flush=True)
    finally:
        stop.set()
        sampler.join()
    assert peaks.get("upload") and peaks.get("download"), peaks
    print("Peak sampled anonymous MiB:", {p: round(v / MIB, 1) for p, v in peaks.items()})
    assert max(peaks.values()) < LIMIT, peaks


if __name__ == "__main__":
    run(sys.argv[1])
