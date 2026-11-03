import pytest
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from garmin_gateway import app as app_module
from garmin_gateway.rest import routes as rest

from .conftest import API_KEY, DOMAIN, REST_KEY


def _bearer(key):
    return {"Authorization": f"Bearer {key}"}


def test_rest_routes_are_mounted_under_api_v1():
    mounts = [r for r in app_module.app.routes if isinstance(r, Mount) and r.path == "/api/v1"]
    assert len(mounts) == 1
    assert mounts[0].routes == rest.routes


@pytest.fixture
def rest_client(monkeypatch):
    """App built with a stand-in REST route (no lifespan: /api/v1 does not need the MCP session manager)."""

    async def ping(_):
        return JSONResponse({"pong": True})

    monkeypatch.setattr(rest, "routes", [Route("/ping", ping)])
    return TestClient(app_module.build_app(), base_url=f"https://{DOMAIN}")


def test_rest_route_is_served_with_rest_key(rest_client):
    r = rest_client.get("/api/v1/ping", headers=_bearer(REST_KEY))
    assert r.status_code == 200
    assert r.json() == {"pong": True}


def test_rest_route_refuses_mcp_key(rest_client):
    assert rest_client.get("/api/v1/ping", headers=_bearer(API_KEY)).status_code == 401


def test_health_stays_open(http):
    assert http.get("/health").json() == {"status": "ok"}


def test_unknown_api_route_requires_rest_key_then_404(http):
    assert http.get("/api/v1/nothing").status_code == 401
    assert http.get("/api/v1/nothing", headers=_bearer(REST_KEY)).status_code == 404


def test_path_outside_known_prefixes_is_404(http):
    r = http.get("/admin", headers=_bearer(API_KEY))
    assert r.status_code == 404
    assert "www-authenticate" not in r.headers


def test_mcp_refuses_rest_key(http):
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **_bearer(REST_KEY)}
    assert http.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401
