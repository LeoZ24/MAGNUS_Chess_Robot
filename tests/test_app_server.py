"""Tests del servidor HTTP de la interfaz (librería estándar, puerto efímero)."""

import http.client
import json

import pytest

from magnus.app.controller import MagnusController
from magnus.app.server import AppServer
from magnus.app.settings import AppSettings


@pytest.fixture
def served():
    ctrl = MagnusController(AppSettings(), synthetic=True, engine_enabled=False,
                            voice_enabled=False)
    ctrl.start_without_thread()
    for _ in range(3):
        ctrl.step()
    server = AppServer(ctrl, host="127.0.0.1", port=0).start()
    yield ctrl, server
    server.stop()
    ctrl.shutdown()


def _conn(server):
    return http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)


def test_index_and_static_files(served):
    _, server = served
    conn = _conn(server)
    conn.request("GET", "/")
    res = conn.getresponse()
    body = res.read().decode("utf-8")
    assert res.status == 200 and "text/html" in res.getheader("Content-Type")
    assert "MAGNUS" in body and "/static/app.js" in body
    for name, marker in (("app.js", "EventSource"), ("app.css", "--accent")):
        conn.request("GET", f"/static/{name}")
        res = conn.getresponse()
        assert res.status == 200 and marker in res.read().decode("utf-8")
    conn.request("GET", "/static/../settings.py")     # fuera de static/
    res = conn.getresponse()
    res.read()
    assert res.status == 404
    assert server.url.startswith("http://localhost:")


def test_state_and_commands(served):
    ctrl, server = served
    conn = _conn(server)
    conn.request("GET", "/api/state")
    res = conn.getresponse()
    snap = json.loads(res.read())
    assert res.status == 200 and snap["phase"] == "setup" and "board" in snap

    body = json.dumps({"name": "set_difficulty", "params": {"level": "HARD"}})
    conn.request("POST", "/api/command", body=body, headers={"Content-Type": "application/json"})
    res = conn.getresponse()
    assert res.status == 200 and json.loads(res.read())["ok"] is True
    ctrl.step()
    assert ctrl.snapshot()["settings"]["difficulty"] == "HARD"

    conn.request("POST", "/api/command", body=json.dumps({"name": "nope"}),
                 headers={"Content-Type": "application/json"})
    res = conn.getresponse()
    assert res.status == 400 and json.loads(res.read())["ok"] is False

    conn.request("POST", "/api/command", body="{garbage", headers={"Content-Type": "application/json"})
    res = conn.getresponse()
    res.read()
    assert res.status == 400

    conn.request("GET", "/api/nada")
    res = conn.getresponse()
    res.read()
    assert res.status == 404


def test_camera_snapshot_and_mjpeg_stream(served):
    _, server = served
    conn = _conn(server)
    conn.request("GET", "/stream/camera.jpg")
    res = conn.getresponse()
    data = res.read()
    assert res.status == 200 and res.getheader("Content-Type") == "image/jpeg"
    assert data[:2] == b"\xff\xd8"

    server.single_frame = True                 # el stream cierra tras un frame
    conn = _conn(server)
    conn.request("GET", "/stream/camera.mjpg")
    res = conn.getresponse()
    assert "multipart/x-mixed-replace" in res.getheader("Content-Type")
    chunk = res.read()
    assert b"--magnusframe" in chunk and b"Content-Type: image/jpeg" in chunk
    assert b"\xff\xd8" in chunk


def test_events_stream_sends_snapshot(served):
    _, server = served
    conn = _conn(server)
    conn.request("GET", "/api/events")
    res = conn.getresponse()
    assert res.status == 200 and "text/event-stream" in res.getheader("Content-Type")
    line = res.fp.readline()
    assert line.startswith(b"data: ")
    snap = json.loads(line[len(b"data: "):])
    assert snap["phase"] == "setup"
    conn.close()
