import json
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import TypeAdapter

from garmin_gateway.mcp import server, workout_tools

from .conftest import API_KEY
from .garmin_fakes import fake_garmin  # noqa: F401  (fixture)

# Reference payloads: cases.json holds tool inputs, expected.json what Garmin Connect must receive for each.
PAYLOADS = Path(__file__).parent / "workout_payloads"
CASES = json.loads((PAYLOADS / "cases.json").read_text())
EXPECTED = json.loads((PAYLOADS / "expected.json").read_text())

WORKOUT_TOOLS = {
    "push_workout",
    "list_workouts",
    "get_workout",
    "schedule_existing_workout",
    "move_scheduled_workout",
    "get_scheduled_workouts",
}


async def call(_tool, **args):
    """Call a tool through FastMCP (argument validation runs) and return what the client receives as data."""
    result = await server.mcp.call_tool(_tool, args)
    if isinstance(result, tuple):
        return result[1]
    return json.loads(result[0].text)


def _steps(raw):
    return TypeAdapter(list[workout_tools.WorkoutStep]).validate_python(raw)


def _canonical(data):
    # JSON text keeps the int/float distinction that == on Python numbers ignores.
    return json.dumps(data, sort_keys=True)


# --- Payload characterization -------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("case", sorted(CASES))
async def test_push_workout_sends_reference_payload(fake_garmin, case):
    result = await call("push_workout", date="2026-11-10", name=f"Seance {case}", steps=CASES[case])

    (garmin,) = fake_garmin.instances
    (workout,) = garmin.uploaded
    assert _canonical(workout.to_dict()) == _canonical(EXPECTED[case]["payload"])
    assert garmin.calls[-1] == ("schedule_workout", 123, "2026-11-10")
    assert result == EXPECTED[case]["result"]


@pytest.mark.parametrize("case", sorted(CASES))
def test_total_duration_matches_reference(case):
    assert workout_tools._total_duration(_steps(CASES[case])) == EXPECTED[case]["total"]


def test_pace_target_is_sent_in_meters_per_second():
    target = TypeAdapter(workout_tools.Target).validate_python(
        {"type": "pace_zone", "allure_rapide_min_par_km": 4.5, "allure_lente_min_par_km": 5.0}
    )
    target_type, fast, slow = workout_tools._target_fields(target)
    assert target_type == {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace_zone", "displayOrder": 6}
    assert (fast, slow) == (3.7037, 3.3333)


def test_integer_inputs_become_floats():
    (step,) = _steps(CASES["hr_zone"])
    _, low, high = workout_tools._target_fields(step.target)
    _, end_value = workout_tools._end_condition_dict(step.end_condition)
    assert all(type(v) is float for v in (low, high, end_value))


def test_targets_are_set_on_the_executable_step_not_on_target_type():
    built, next_order = workout_tools._build_steps(_steps(CASES["power"]))
    (step,) = built
    assert next_order == 2
    assert step.targetValueOne == 250.0 and step.targetValueTwo == 300.0
    assert "targetValueOne" not in step.targetType


def test_repeat_group_numbers_inner_steps_from_one():
    built, next_order = workout_tools._build_steps(_steps(CASES["full_session"]))
    assert next_order == 6
    repeat = built[1]
    assert repeat.stepOrder == 2
    assert repeat.numberOfIterations == 6
    assert [s.stepOrder for s in repeat.workoutSteps] == [1, 2]


# --- Other tools -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_workouts(fake_garmin):
    # FastMCP sends a list as one content block per item.
    content = await server.mcp.call_tool("list_workouts", {"limit": 5})
    assert [json.loads(c.text) for c in content] == [{"workoutId": 1, "workoutName": "Easy"}]
    assert fake_garmin.instances[0].calls == [("get_workouts", 0, 5)]


@pytest.mark.anyio
async def test_list_workouts_default_limit(fake_garmin):
    await call("list_workouts")
    assert fake_garmin.instances[0].calls == [("get_workouts", 0, 50)]


@pytest.mark.anyio
async def test_get_workout(fake_garmin):
    assert await call("get_workout", workout_id=42) == {"workoutId": 42}
    assert fake_garmin.instances[0].calls == [("get_workout_by_id", 42)]


@pytest.mark.anyio
async def test_schedule_existing_workout(fake_garmin):
    result = await call("schedule_existing_workout", workout_id=42, date="2026-11-12")
    assert result == {"workout_id": 42, "date": "2026-11-12", "scheduled": True}
    assert fake_garmin.instances[0].calls == [("schedule_workout", 42, "2026-11-12")]


@pytest.mark.anyio
async def test_move_scheduled_workout(fake_garmin):
    result = await call("move_scheduled_workout", scheduled_id=9, new_date="2026-11-14")
    assert result == {"workout_id": 77, "scheduled_id": 9, "new_date": "2026-11-14"}
    assert fake_garmin.instances[0].calls == [
        ("get_scheduled_workout_by_id", 9),
        ("unschedule_workout", 9),
        ("schedule_workout", 77, "2026-11-14"),
    ]


@pytest.mark.anyio
async def test_move_scheduled_workout_without_workout_id_keeps_the_calendar_entry(fake_garmin, monkeypatch):
    monkeypatch.setattr(fake_garmin, "get_scheduled_workout_by_id", lambda self, sid: {})
    with pytest.raises(ToolError, match="no workout"):
        await call("move_scheduled_workout", scheduled_id=9, new_date="2026-11-14")
    assert fake_garmin.instances[0].calls == []


@pytest.mark.anyio
async def test_get_scheduled_workouts(fake_garmin):
    result = await call("get_scheduled_workouts", year=2026, month=11)
    assert result == {"calendarItems": [{"id": 9, "date": "2026-11-05"}]}
    assert fake_garmin.instances[0].calls == [("get_scheduled_workouts", 2026, 11)]


@pytest.mark.anyio
async def test_push_workout_without_workout_id_is_an_error(fake_garmin, monkeypatch):
    def upload(self, workout):
        return {"status": "ok"}

    monkeypatch.setattr(fake_garmin, "upload_running_workout", upload)
    with pytest.raises(ToolError, match="no workout id"):
        await call("push_workout", date="2026-11-10", name="x", steps=CASES["power"])
    assert fake_garmin.instances[0].calls == []


@pytest.mark.anyio
async def test_invalid_steps_never_reach_garmin(fake_garmin):
    bad = [{"type": "interval", "end_condition": {"type": "time"}}]
    with pytest.raises(ToolError):
        await call("push_workout", date="2026-11-10", name="x", steps=bad)
    assert fake_garmin.instances == []


# --- Registration and errors through the HTTP app ------------------------------------------------

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}


def _rpc_result(response):
    data = next(line[5:] for line in response.text.splitlines() if line.startswith("data:"))
    return json.loads(data)["result"]


@pytest.mark.anyio
async def test_workout_tools_are_registered():
    tools = {t.name for t in await server.mcp.list_tools()}
    assert WORKOUT_TOOLS <= tools
    assert len(tools) == 33


@pytest.mark.anyio
async def test_push_workout_schema_keeps_step_models():
    (tool,) = [t for t in await server.mcp.list_tools() if t.name == "push_workout"]
    assert set(tool.inputSchema["required"]) == {"date", "name", "steps"}
    assert "Push a structured running workout" in tool.description


def test_mcp_lists_read_and_write_tools(http):
    r = http.post("/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in _rpc_result(r)["tools"]}
    assert len(names) == 33
    assert WORKOUT_TOOLS <= names


def test_missing_tokens_give_a_clean_tool_error(http):
    # conftest points GARMIN_TOKEN_STORE at a file that does not exist.
    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_workouts", "arguments": {}}}
    result = _rpc_result(http.post("/mcp", headers=MCP_HEADERS, json=body))
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "python -m garmin_gateway.login" in text
    assert "Traceback" not in text


def test_server_instructions_describe_read_and_write_backends():
    text = server.mcp.instructions
    assert "official Garmin Personal API" in text
    assert "Garmin Connect" in text and "workout" in text.lower()
    assert "python -m garmin_gateway.login" in text
