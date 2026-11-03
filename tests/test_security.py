import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from garmin_gateway import security

from .conftest import API_KEY, REST_KEY


def _scope(headers=None, query=b"", path="/mcp", client=("10.0.0.1", 1234)):
    return {
        "type": "http",
        "path": path,
        "headers": [(k.lower(), v) for k, v in (headers or {}).items()],
        "query_string": query,
        "client": client,
    }


@pytest.fixture
def client():
    async def ok(_):
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[
            Route("/mcp", ok, methods=["GET", "POST"]),
            Route("/health", ok),
            Route("/api/v1/ping", ok),
            # Served by the app but outside every known prefix: the middleware must hide it.
            Route("/other", ok),
            Route("/mcpx", ok),
            Route("/api", ok),
        ]
    )
    app.add_middleware(security.ApiKeyMiddleware)
    return TestClient(app)


# --- _valid ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers,query",
    [
        ({b"authorization": f"Bearer {API_KEY}".encode()}, b""),
        ({b"authorization": f"bearer   {API_KEY} ".encode()}, b""),
        # Unrelated query parameters don't matter.
        ({b"authorization": f"Bearer {API_KEY}".encode()}, b"api_key=wrong"),
    ],
)
def test_valid_accepts_correct_key(headers, query):
    assert security._valid(_scope(headers, query), API_KEY)


@pytest.mark.parametrize(
    "headers,query",
    [
        ({}, b""),
        ({b"authorization": b"Bearer wrong"}, b""),
        ({b"authorization": API_KEY.encode()}, b""),  # missing "Bearer "
        ({b"authorization": f"Basic {API_KEY}".encode()}, b""),
        ({}, f"key={API_KEY}".encode()),
        ({}, f"api_key={API_KEY[:-1]}".encode()),
        ({}, f"api_key={API_KEY}x".encode()),
        # The key is only accepted in the Authorization header, never in the URL.
        ({}, f"api_key={API_KEY}".encode()),
        ({}, f"api-key={API_KEY}".encode()),
        ({b"authorization": b"Bearer wrong"}, f"api_key={API_KEY}".encode()),
    ],
)
def test_valid_rejects_bad_key(headers, query):
    assert not security._valid(_scope(headers, query), API_KEY)


def test_non_ascii_key_matches_as_utf8():
    key = "clé-" + "x" * 30
    assert security._valid(_scope({b"authorization": f"Bearer {key}".encode()}), key)
    assert not security._valid(_scope({b"authorization": f"Bearer {key}".encode("latin-1")}), key)


def test_non_utf8_header_does_not_crash():
    assert not security._valid(_scope({b"authorization": b"Bearer \xe9\xff"}, b"api_key=\xe9"), API_KEY)


# --- Key per path prefix ----------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/mcp", "mcp"),
        ("/mcp/", "mcp"),
        ("/api/", "rest"),
        ("/api/v1/activities/1/series", "rest"),
        ("/health", "open"),
        ("/", None),
        ("/other", None),
        ("/mcpx", None),
        ("/MCP", None),
        ("/api", None),
        ("/apix/v1", None),
        ("/health/x", None),
    ],
)
def test_required_key_by_prefix(monkeypatch, path, expected):
    monkeypatch.setattr(security, "MCP_API_KEY", "mcp")
    monkeypatch.setattr(security, "REST_API_KEY", "rest")
    assert security._required_key(path) == (security.OPEN if expected == "open" else expected)


# --- Middleware -------------------------------------------------------------------


def test_health_is_open(client):
    assert client.get("/health").status_code == 200


def test_missing_key_is_rejected(client):
    r = client.get("/mcp")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert API_KEY not in r.text


def test_good_key_passes(client):
    assert client.get("/mcp", headers={"Authorization": f"Bearer {API_KEY}"}).text == "ok"


@pytest.mark.parametrize("param", ["api_key", "api-key"])
def test_key_in_url_is_rejected(client, param):
    assert client.get(f"/mcp?{param}={API_KEY}").status_code == 401


def test_failed_attempts_never_lock_out_the_valid_key(client):
    # Regression: a per-IP lockout let anyone sharing the client's IP block it, even with the right key.
    ip = {"X-Real-IP": "9.9.9.9"}
    for _ in range(50):
        assert client.get("/mcp", headers=ip).status_code == 401
    assert client.get("/mcp", headers={**ip, "Authorization": f"Bearer {API_KEY}"}).status_code == 200


@pytest.mark.anyio
async def test_lifespan_passes_through():
    called = []

    async def inner(scope, receive, send):
        called.append(scope["type"])

    await security.ApiKeyMiddleware(inner)({"type": "lifespan"}, None, None)
    assert called == ["lifespan"]


# --- Two keys, two entry points ---------------------------------------------------


def _bearer(key):
    return {"Authorization": f"Bearer {key}"}


def test_rest_key_opens_api(client):
    assert client.get("/api/v1/ping", headers=_bearer(REST_KEY)).text == "ok"


def test_api_requires_key(client):
    r = client.get("/api/v1/ping")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.json() == {"error": "Unauthorized"}


def test_mcp_key_is_refused_on_api(client):
    assert client.get("/api/v1/ping", headers=_bearer(API_KEY)).status_code == 401


def test_rest_key_is_refused_on_mcp(client):
    assert client.get("/mcp", headers=_bearer(REST_KEY)).status_code == 401


@pytest.mark.parametrize("path", ["/other", "/mcpx", "/api", "/", "/nothing/here"])
@pytest.mark.parametrize("key", [None, API_KEY, REST_KEY])
def test_unknown_path_is_404_without_asking_for_a_key(client, path, key):
    r = client.get(path, headers=_bearer(key) if key else {})
    assert r.status_code == 404
    assert "www-authenticate" not in r.headers
    assert r.json() == {"error": "Not found"}


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/mcp", "/api/v1/ping", "/health", "/other"])
async def test_websocket_is_refused_on_every_path(path):
    called, sent = [], []

    async def inner(scope, receive, send):
        called.append(scope)

    async def send(message):
        sent.append(message)

    await security.ApiKeyMiddleware(inner)({"type": "websocket", "path": path, "headers": []}, None, send)
    assert not called
    assert sent == [{"type": "websocket.close", "code": 1008}]
