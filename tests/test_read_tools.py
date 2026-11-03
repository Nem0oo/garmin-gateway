import json

import pytest

from garmin_gateway.garmin.official import GarminError
from garmin_gateway.mcp import read_tools, server

from .conftest import API_KEY

READ_TOOLS = {
    "list_activities", "get_activity", "get_activity_workouts", "get_activity_file",
    "get_wellness_activity_summaries", "get_daily_summary", "get_daily_summaries", "get_steps",
    "get_stress", "get_hydration", "get_sleep", "get_sleep_summaries", "get_hrv", "get_hrv_summaries",
    "get_heart_rate", "get_body_battery", "get_spo2", "get_weight", "get_weight_entries",
    "get_weight_goals", "get_fitness_age", "get_heart_rate_zones", "get_user_settings",
    "get_nutrition_log", "get_menstrual_cycle_summary", "get_menstrual_cycle_day",
}
WRITE_TOOLS = {
    "push_workout", "list_workouts", "get_workout",
    "schedule_existing_workout", "move_scheduled_workout", "get_scheduled_workouts",
}
ALL_TOOLS = READ_TOOLS | WRITE_TOOLS | {"server_health"}


@pytest.fixture
def calls(monkeypatch):
    """Capture what the tools send to Garmin instead of calling it."""
    recorded = []

    async def fake_get_json(path, params=None):
        recorded.append((path, params))
        return {"path": path}

    async def fake_get_file(path):
        recorded.append((path, None))
        return {"base64": ""}

    monkeypatch.setattr(read_tools.client, "get_json", fake_get_json)
    monkeypatch.setattr(read_tools.client, "get_file", fake_get_file)
    return recorded


# --- Date helpers --------------------------------------------------------------------


def test_range_accepts_inclusive_maximum():
    read_tools._range("2026-01-01", "2026-01-28", 28)
    read_tools._range("2026-01-01", "2026-01-01", 1)


@pytest.mark.parametrize(
    "start,end,match",
    [
        ("2026-01-01", "2026-01-29", "maximum 28 days"),
        ("2026-01-02", "2026-01-01", "before start"),
        ("2026-02-30", "2026-03-01", "Invalid date"),
        ("2026-13-01", "2026-13-02", "Invalid date"),
    ],
)
def test_range_rejects(start, end, match):
    with pytest.raises(GarminError, match=match):
        read_tools._range(start, end, 28)


# --- server_health -----------------------------------------------------------------------


@pytest.mark.parametrize("state,expected", [("file", True), ("missing", False), ("directory", False)])
def test_server_health_reports_write_tokens(tmp_path, monkeypatch, state, expected):
    store = tmp_path / "garmin_tokens.json"
    if state == "file":
        store.write_text("{}")
    elif state == "directory":
        store.mkdir()
    monkeypatch.setattr(server, "GARMIN_TOKEN_STORE", str(store))
    assert server.server_health() == {"status": "ok", "write_tokens": expected}


@pytest.mark.anyio
async def test_server_health_through_mcp_never_calls_garmin(calls):
    result = await server.mcp.call_tool("server_health", {})
    content = result[0] if isinstance(result, tuple) else result
    assert json.loads(content[0].text) == {"status": "ok", "write_tokens": False}
    assert calls == []


# --- Tools (called through FastMCP, so argument validation runs) ----------------------


async def call(name, **args):
    return await server.mcp.call_tool(name, args)


@pytest.mark.anyio
async def test_all_tools_are_registered():
    tools = [t.name for t in await server.mcp.list_tools()]
    assert len(tools) == 33
    assert set(tools) == ALL_TOOLS


@pytest.mark.anyio
@pytest.mark.parametrize(
    "tool,args,path,params",
    [
        ("get_activity", {"activity_id": 42}, "/activity-service/activity/42", None),
        ("get_activity_workouts", {"activity_id": 42}, "/activity-service/activity/42/workouts", None),
        ("get_activity_file", {"activity_id": 42}, "/download-service/files/activity/42", None),
        ("get_daily_summary", {"calendar_date": "2026-10-01"}, "/usersummary-service/usersummary/daily", {"calendarDate": "2026-10-01"}),
        ("get_steps", {"start_date": "2026-10-01", "end_date": "2026-10-07"}, "/usersummary-service/stats/steps/daily/2026-10-01/2026-10-07", None),
        ("get_hrv", {"calendar_date": "2026-10-01"}, "/hrv-service/hrv/2026-10-01", None),
        ("get_hrv_summaries", {"from_date": "2026-10-01", "to_date": "2026-10-07"}, "/hrv-service/hrv/daily/2026-10-01/2026-10-07", None),
        ("get_sleep", {"calendar_date": "2026-10-01"}, "/sleep-service/sleep/dailySleep", {"date": "2026-10-01"}),
        (
            "get_body_battery",
            {"start_date": "2026-10-01", "end_date": "2026-10-02", "chunks": 4},
            "/wellness-service/wellness/bodyBattery/reports/daily",
            {"startDate": "2026-10-01", "endDate": "2026-10-02", "chunks": 4},
        ),
        ("get_weight", {"start_date": "2026-01-01", "end_date": "2026-10-01"}, "/weight-service/weight/range/2026-01-01/2026-10-01", {"includeAll": False}),
        ("get_heart_rate_zones", {}, "/biometric-service/heartRateZones/v2", {"generateDefaults": True}),
        ("get_user_settings", {}, "/userprofile-service/userprofile/settings", None),
    ],
)
async def test_tool_requests(calls, tool, args, path, params):
    await call(tool, **args)
    assert calls == [(path, params)]


@pytest.mark.anyio
async def test_list_activities_maps_parameters(calls):
    await call("list_activities", limit=5, start_date="2026-10-01", activity_type="running", favorite=True, min_distance_m=1000)
    path, params = calls[0]
    assert path == "/activitylist-service/activities/search/activities"
    assert params["limit"] == 5
    assert params["startDate"] == "2026-10-01"
    assert params["activityType"] == "running"
    assert params["favorite"] == 1  # the API expects 1/0, not true/false
    assert params["minDistance"] == 1000
    assert params["sortOrder"] == "desc"
    assert params["endDate"] is None  # dropped later by the client


@pytest.mark.anyio
async def test_list_activities_favorite_false(calls):
    await call("list_activities", favorite=False)
    assert calls[0][1]["favorite"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "tool,args",
    [
        ("get_sleep", {"calendar_date": "../../admin"}),
        ("get_hrv", {"calendar_date": "2026-10-01/../../x"}),
        ("get_steps", {"start_date": "2026-10-01", "end_date": "2026-10-29"}),  # 29 days
        ("get_activity", {"activity_id": "12/../../x"}),
        ("list_activities", {"limit": 101}),
        ("list_activities", {"limit": 0}),
        ("list_activities", {"sort_order": "random"}),
        ("get_body_battery", {"start_date": "2026-10-01", "end_date": "2026-10-02", "chunks": 49}),
    ],
)
async def test_invalid_arguments_never_reach_garmin(calls, tool, args):
    with pytest.raises(Exception):
        await call(tool, **args)
    assert calls == []


# --- HTTP app (auth + MCP transport + Host validation) ---------------------------------

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def _rpc_result(response):
    data = next(line[5:] for line in response.text.splitlines() if line.startswith("data:"))
    return json.loads(data)["result"]


def test_health(http):
    assert http.get("/health").json() == {"status": "ok"}


def test_mcp_requires_key(http):
    assert http.post("/mcp", headers=MCP_HEADERS, json=LIST_TOOLS).status_code == 401


def test_mcp_rejects_key_in_url(http):
    assert http.post(f"/mcp?api_key={API_KEY}", headers=MCP_HEADERS, json=LIST_TOOLS).status_code == 401


def test_mcp_lists_tools_with_key(http):
    r = http.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {API_KEY}"}, json=LIST_TOOLS)
    assert r.status_code == 200
    names = [t["name"] for t in _rpc_result(r)["tools"]]
    assert len(names) == 33
    assert set(names) == ALL_TOOLS


def test_mcp_tool_error_is_reported_without_crash(http, monkeypatch):
    async def failing(path, params=None):
        raise GarminError("Garmin API rate limit reached, retry later")

    monkeypatch.setattr(read_tools.client, "get_json", failing)
    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_user_settings", "arguments": {}}}
    r = http.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {API_KEY}"}, json=body)
    result = _rpc_result(r)
    assert result["isError"] is True
    assert "rate limit" in result["content"][0]["text"]


@pytest.mark.parametrize("headers", [{"Host": "evil.com"}, {"Origin": "https://evil.com"}])
def test_dns_rebinding_protection(http, headers):
    r = http.post("/mcp", headers={**MCP_HEADERS, **headers, "Authorization": f"Bearer {API_KEY}"}, json=LIST_TOOLS)
    assert r.status_code in (403, 421)
