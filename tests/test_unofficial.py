import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from garminconnect.exceptions import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

from garmin_gateway import config
from garmin_gateway.garmin import unofficial
from garmin_gateway.garmin.unofficial import UnofficialError

from .garmin_fakes import fake_garmin  # noqa: F401  (fixture)

LOGIN_HINT = "docker exec -it <container> python -m garmin_gateway.login"
ROOT = Path(__file__).resolve().parent.parent


async def _call(fn):
    return await unofficial.client.call(fn)


def test_no_garmin_client_before_the_first_call(fake_garmin):
    unofficial.UnofficialClient()
    assert fake_garmin.instances == []


@pytest.mark.anyio
async def test_first_call_logs_in_with_the_token_store(fake_garmin):
    assert await _call(lambda g: g.get_workouts(0, 3)) == [{"workoutId": 1, "workoutName": "Easy"}]
    (garmin,) = fake_garmin.instances
    assert garmin.args == () and garmin.kwargs == {}
    assert garmin.logins == [config.GARMIN_TOKEN_STORE]


@pytest.mark.anyio
async def test_client_is_reused_between_calls(fake_garmin):
    await _call(lambda g: g.get_workouts(0, 1))
    await _call(lambda g: g.get_workout_by_id(5))
    (garmin,) = fake_garmin.instances
    assert len(garmin.logins) == 1


@pytest.mark.anyio
async def test_garmin_calls_run_outside_the_event_loop_thread(fake_garmin):
    loop_thread = threading.get_ident()
    await _call(lambda g: g.get_workouts(0, 1))
    (garmin,) = fake_garmin.instances
    assert garmin.threads and loop_thread not in garmin.threads


@pytest.mark.anyio
async def test_missing_token_file_points_to_login(fake_garmin, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GARMIN_TOKEN_STORE", str(tmp_path / "absent.json"))
    with pytest.raises(UnofficialError) as exc:
        await _call(lambda g: g.get_workouts(0, 1))
    assert LOGIN_HINT in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
    assert fake_garmin.instances == []


@pytest.mark.anyio
async def test_rejected_tokens_point_to_login_and_are_retried_next_time(fake_garmin, monkeypatch):
    monkeypatch.setattr(fake_garmin, "login_error", GarminConnectAuthenticationError("token secret-abc rejected"))
    with pytest.raises(UnofficialError) as exc:
        await _call(lambda g: g.get_workouts(0, 1))
    assert LOGIN_HINT in str(exc.value)
    assert "secret-abc" not in str(exc.value)
    assert exc.value.__suppress_context__

    monkeypatch.setattr(fake_garmin, "login_error", None)
    await _call(lambda g: g.get_workouts(0, 1))
    assert len(fake_garmin.instances) == 2


@pytest.mark.anyio
async def test_auth_error_during_a_call_drops_the_cached_client(fake_garmin):
    def expired(_):
        raise GarminConnectAuthenticationError("refresh failed")

    await _call(lambda g: g.get_workouts(0, 1))
    with pytest.raises(UnofficialError, match="garmin_gateway.login"):
        await _call(expired)
    await _call(lambda g: g.get_workouts(0, 1))
    assert len(fake_garmin.instances) == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error,message",
    [
        (GarminConnectTooManyRequestsError("429 for url https://x?token=abc"), "rate limit"),
        (GarminConnectNotFoundError("404 for url https://x?token=abc"), "not found"),
        (GarminConnectConnectionError("boom https://x?token=abc"), "Garmin Connect request failed"),
        (RuntimeError("token=abc"), "Garmin Connect request failed (RuntimeError)"),
    ],
)
async def test_garmin_errors_become_clean_messages(fake_garmin, error, message):
    def failing(_):
        raise error

    with pytest.raises(UnofficialError) as exc:
        await _call(failing)
    assert message in str(exc.value)
    assert "abc" not in str(exc.value)
    assert exc.value.__suppress_context__
    assert len(fake_garmin.instances) == 1  # the client stays cached


@pytest.mark.anyio
async def test_connection_error_reports_http_status(fake_garmin):
    class Response:
        status_code = 503

    error = GarminConnectConnectionError("boom")
    error.response = Response()

    def failing(_):
        raise error

    with pytest.raises(UnofficialError, match=r"HTTP 503"):
        await _call(failing)


@pytest.mark.anyio
async def test_invalid_argument_is_reported(fake_garmin):
    def failing(_):
        raise ValueError("month must be between 1 and 12, got: 13")

    with pytest.raises(UnofficialError, match="month must be between 1 and 12"):
        await _call(failing)


# --- The service does not need garminconnect to start or to read ----------------------------------


def _run(code):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)


def test_startup_does_not_import_garminconnect():
    r = _run(
        "import sys, asyncio\n"
        "import garmin_gateway.app\n"
        "from garmin_gateway.mcp import server\n"
        "names = {t.name for t in asyncio.run(server.mcp.list_tools())}\n"
        "assert len(names) == 33, len(names)\n"
        "assert 'garminconnect' not in sys.modules\n"
    )
    assert r.returncode == 0, r.stderr


def test_read_tools_work_without_garminconnect_and_write_tools_fail_cleanly():
    r = _run(
        "import sys, asyncio\n"
        "sys.modules['garminconnect'] = None\n"
        "from mcp.server.fastmcp.exceptions import ToolError\n"
        "from garmin_gateway.mcp import read_tools, server\n"
        "async def fake(path, params=None):\n"
        "    return {'ok': True}\n"
        "read_tools.client.get_json = fake\n"
        "async def main():\n"
        "    await server.mcp.call_tool('get_user_settings', {})\n"
        "    await server.mcp.call_tool('server_health', {})\n"
        "    try:\n"
        "        await server.mcp.call_tool('list_workouts', {})\n"
        "    except ToolError as exc:\n"
        "        print(exc)\n"
        "asyncio.run(main())\n"
    )
    assert r.returncode == 0, r.stderr
    assert "garminconnect is not available" in r.stdout
