import json
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from garmin_gateway import app as app_module
from garmin_gateway.config import TOKEN_PATH
from garmin_gateway.garmin.official import GarminClient
from garmin_gateway.mcp import read_tools
from garmin_gateway.rest import routes

from .conftest import API_KEY, DOMAIN, REST_KEY

FIXTURES = Path(__file__).parent / "fixtures"
ACTIVITY_ID = 21234567890
URL = f"/api/v1/activities/{ACTIVITY_ID}/series"
FILE_PATH = f"/download-service/files/activity/{ACTIVITY_ID}"
AUTH = {"Authorization": f"Bearer {REST_KEY}"}


class FakeGarmin:
    """httpx transport standing in for Garmin: token endpoint + one scripted file response."""

    def __init__(self, response: httpx.Response, token_response: httpx.Response | None = None):
        self.response = response
        self.token_response = token_response or httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            return self.token_response
        self.paths.append(request.url.path)
        return self.response


@pytest.fixture
def garmin(monkeypatch):
    """Installs a Garmin client on a fake transport; call it with the file response to serve."""

    def install(response: httpx.Response, **kwargs) -> FakeGarmin:
        fake = FakeGarmin(response, **kwargs)
        client = GarminClient()
        client._http = httpx.AsyncClient(base_url="https://garmin.test", transport=httpx.MockTransport(fake))
        monkeypatch.setattr(read_tools, "client", client)
        return fake

    return install


@pytest.fixture
def api():
    # No lifespan: /api/v1 does not need the MCP session manager (see test_app.py).
    return TestClient(app_module.build_app(), base_url=f"https://{DOMAIN}")


def _zip_response(name: str = "activity_power.zip") -> httpx.Response:
    return httpx.Response(200, content=(FIXTURES / name).read_bytes(), headers={"content-type": "application/zip"})


def _assert_error(r: httpx.Response, status: int) -> str:
    assert r.status_code == status
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert set(body) == {"error"} and isinstance(body["error"], str) and body["error"]
    return body["error"]


# --- Success ------------------------------------------------------------------------------


def test_series_matches_reference_response(api, garmin):
    fake = garmin(_zip_response())
    r = api.get(URL, headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == json.loads((FIXTURES / "series_expected.json").read_text())
    assert fake.paths == [FILE_PATH]


def test_series_absent_from_file_is_null(api, garmin):
    garmin(_zip_response("activity_no_power.zip"))
    body = api.get(URL, headers=AUTH).json()
    assert body["activity_id"] == str(ACTIVITY_ID)
    assert body["series"]["power_w"] is None
    assert len(body["series"]["hr_bpm"]) == len(body["series"]["timestamps"]) == 30


def test_only_get_is_served(api, garmin):
    garmin(_zip_response())
    assert api.post(URL, headers=AUTH).status_code == 405
    assert api.delete(URL, headers=AUTH).status_code == 405


# --- Errors -------------------------------------------------------------------------------


@pytest.mark.parametrize("activity_id", ["abc", "0", "-5", "1.5", "1e3", "00", "1" * 25, "%20"])
def test_invalid_activity_id_is_400(api, garmin, activity_id):
    fake = garmin(_zip_response())
    _assert_error(api.get(f"/api/v1/activities/{activity_id}/series", headers=AUTH), 400)
    assert fake.paths == []


def test_unknown_activity_is_404(api, garmin):
    garmin(httpx.Response(404, text="<html>not found</html>"))
    _assert_error(api.get(URL, headers=AUTH), 404)


def test_zip_over_max_fit_bytes_is_413(api, garmin, monkeypatch):
    monkeypatch.setattr(routes, "MAX_FIT_BYTES", 100)
    garmin(_zip_response())
    assert "100 bytes" in _assert_error(api.get(URL, headers=AUTH), 413)


def test_fit_too_large_once_unzipped_is_413(api, garmin, monkeypatch):
    monkeypatch.setattr(routes, "MAX_UNZIPPED_BYTES", 100)
    garmin(_zip_response())
    _assert_error(api.get(URL, headers=AUTH), 413)


@pytest.mark.parametrize("content", [b"not a zip", b""])
def test_unusable_file_is_422(api, garmin, content):
    garmin(httpx.Response(200, content=content))
    _assert_error(api.get(URL, headers=AUTH), 422)


@pytest.mark.parametrize(
    "response,message",
    [
        (httpx.Response(500, text="Traceback secret"), "Garmin API error (HTTP 500)"),
        (httpx.Response(429), "rate limit"),
        (httpx.Response(403), "forbidden"),
    ],
)
def test_garmin_error_is_502_with_its_message(api, garmin, response, message):
    garmin(response)
    error = _assert_error(api.get(URL, headers=AUTH), 502)
    assert message in error
    assert "secret" not in error


def test_rejected_personal_key_is_502_without_secret(api, garmin):
    garmin(_zip_response(), token_response=httpx.Response(401, text="garmin-personal-key"))
    r = api.get(URL, headers=AUTH)
    error = _assert_error(r, 502)
    assert "rejected the personal API key" in error
    assert "garmin-personal-key" not in r.text


def test_unexpected_error_is_500_without_trace(api, garmin, monkeypatch):
    garmin(_zip_response())

    def boom(*_):
        raise ValueError("internal detail")

    monkeypatch.setattr(routes.fit, "parse_activity_zip", boom)
    r = TestClient(app_module.build_app(), base_url=f"https://{DOMAIN}", raise_server_exceptions=False).get(URL, headers=AUTH)
    assert r.status_code == 500
    assert "internal detail" not in r.text


# --- Auth -----------------------------------------------------------------------------------


def test_without_key_is_401(api, garmin):
    fake = garmin(_zip_response())
    r = api.get(URL)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert fake.paths == []


def test_mcp_key_is_401(api, garmin):
    fake = garmin(_zip_response())
    assert api.get(URL, headers={"Authorization": f"Bearer {API_KEY}"}).status_code == 401
    assert fake.paths == []


def test_key_in_query_string_is_401(api, garmin):
    garmin(_zip_response())
    assert api.get(f"{URL}?api_key={REST_KEY}").status_code == 401
