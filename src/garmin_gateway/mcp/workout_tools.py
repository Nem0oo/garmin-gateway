"""Workout write tools backed by the unofficial Garmin Connect API (garminconnect).

garminconnect is imported inside the functions that need it, so registering these tools does not load it.
"""

from typing import Annotated, Literal, Union

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from ..garmin import unofficial
from ..garmin.unofficial import UnofficialError

_STEP_TYPE = {
    "warmup":   {"stepTypeId": 1, "stepTypeKey": "warmup",   "displayOrder": 1},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    "interval": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
}

# (conditionTypeId, conditionTypeKey) — from garminconnect.workout.ConditionType
_CONDITION_TYPE = {
    "lap_button":        (1,  "lap.button"),
    "time":              (2,  "time"),
    "distance":          (3,  "distance"),
    "calories":          (4,  "calories"),
    "power":             (5,  "power"),
    "heart_rate":        (6,  "heart.rate"),
    "iterations":        (7,  "iterations"),
    "fixed_rest":        (8,  "fixed.rest"),
    "fixed_repetition":  (9,  "fixed.repetition"),
    "reps":              (10, "reps"),
}

# ── Target types ──────────────────────────────────────────────────────────────

_TARGET_TYPE = {
    "no_target":      (1,  "no.target"),
    "power_zone":     (2,  "power_zone"),
    "cadence":        (3,  "cadence"),
    "heart_rate_zone":(4,  "heart_rate_zone"),
    "speed_zone":     (5,  "speed_zone"),
    "pace_zone":      (6,  "pace_zone"),
    "grade":          (7,  "grade"),
    "heart_rate_lap": (8,  "heart_rate_lap"),
    "power_lap":      (9,  "power_lap"),
    "resistance":     (15, "resistance"),
}


class TargetNone(BaseModel):
    type: Literal["no_target"]


class TargetPace(BaseModel):
    type: Literal["pace_zone"]
    allure_rapide_min_par_km: float = Field(description="Borne rapide en minutes décimales par km. Ex: 4.5 = 4:30/km.")
    allure_lente_min_par_km: float = Field(description="Borne lente en minutes décimales par km. Ex: 5.0 = 5:00/km.")


class TargetHeartRate(BaseModel):
    type: Literal["heart_rate_zone"]
    bpm_min: int
    bpm_max: int


class TargetCadence(BaseModel):
    type: Literal["cadence"]
    spm_min: int = Field(description="Cadence minimale en pas par minute.")
    spm_max: int = Field(description="Cadence maximale en pas par minute.")


class TargetPower(BaseModel):
    type: Literal["power_zone"]
    watts_min: int
    watts_max: int


class TargetSpeed(BaseModel):
    type: Literal["speed_zone"]
    kmh_min: float
    kmh_max: float


class TargetGrade(BaseModel):
    type: Literal["grade"]
    grade_min: float = Field(description="Pente minimale en %.")
    grade_max: float = Field(description="Pente maximale en %.")


class TargetHeartRateLap(BaseModel):
    type: Literal["heart_rate_lap"]
    bpm_min: int
    bpm_max: int


class TargetPowerLap(BaseModel):
    type: Literal["power_lap"]
    watts_min: int
    watts_max: int


class TargetResistance(BaseModel):
    type: Literal["resistance"]
    resistance_min: float
    resistance_max: float


Target = Annotated[
    Union[
        TargetNone, TargetPace, TargetHeartRate, TargetCadence, TargetPower,
        TargetSpeed, TargetGrade, TargetHeartRateLap, TargetPowerLap, TargetResistance,
    ],
    Field(discriminator="type")
]


def _target_fields(target: Target) -> tuple[dict, float | None, float | None]:
    """Returns (targetType dict, targetValueOne, targetValueTwo).
    Values are extra fields on ExecutableStep, NOT inside targetType.
    Pace bounds are sent as speeds in m/s; every value is a float."""
    tid, tkey = _TARGET_TYPE[target.type]
    target_type = {"workoutTargetTypeId": tid, "workoutTargetTypeKey": tkey, "displayOrder": tid}
    range_map = {
        "pace_zone":       lambda t: (round(1000 / (t.allure_rapide_min_par_km * 60), 4), round(1000 / (t.allure_lente_min_par_km * 60), 4)),
        "heart_rate_zone": lambda t: (float(t.bpm_min), float(t.bpm_max)),
        "cadence":         lambda t: (float(t.spm_min), float(t.spm_max)),
        "power_zone":      lambda t: (float(t.watts_min), float(t.watts_max)),
        "speed_zone":      lambda t: (float(t.kmh_min), float(t.kmh_max)),
        "grade":           lambda t: (float(t.grade_min), float(t.grade_max)),
        "heart_rate_lap":  lambda t: (float(t.bpm_min), float(t.bpm_max)),
        "power_lap":       lambda t: (float(t.watts_min), float(t.watts_max)),
        "resistance":      lambda t: (float(t.resistance_min), float(t.resistance_max)),
    }
    if target.type in range_map:
        v1, v2 = range_map[target.type](target)
        return target_type, v1, v2
    return target_type, None, None


# ── End condition types ───────────────────────────────────────────────────────


class EndConditionTime(BaseModel):
    type: Literal["time"]
    duree_minutes: float


class EndConditionDistance(BaseModel):
    type: Literal["distance"]
    distance_km: float


class EndConditionCalories(BaseModel):
    type: Literal["calories"]
    calories: int = Field(description="Objectif en kcal.")


class EndConditionPower(BaseModel):
    type: Literal["power"]
    watts: int


class EndConditionHeartRate(BaseModel):
    type: Literal["heart_rate"]
    bpm: int


class EndConditionLapButton(BaseModel):
    type: Literal["lap_button"]


class EndConditionIterations(BaseModel):
    type: Literal["iterations"]
    count: int


class EndConditionFixedRest(BaseModel):
    type: Literal["fixed_rest"]
    duree_minutes: float


class EndConditionFixedRepetition(BaseModel):
    type: Literal["fixed_repetition"]
    count: int


class EndConditionReps(BaseModel):
    type: Literal["reps"]
    count: int


EndCondition = Annotated[
    Union[
        EndConditionTime, EndConditionDistance, EndConditionCalories,
        EndConditionPower, EndConditionHeartRate, EndConditionLapButton,
        EndConditionIterations, EndConditionFixedRest, EndConditionFixedRepetition,
        EndConditionReps,
    ],
    Field(discriminator="type")
]


class SimpleStep(BaseModel):
    type: Literal["warmup", "interval", "recovery", "cooldown"]
    end_condition: EndCondition
    target: Target | None = Field(default=None, description="Objectif du step. Optionnel — omit for no target.")


class RepeatStep(BaseModel):
    type: Literal["repeat"]
    repetitions: int
    steps: list[SimpleStep]


WorkoutStep = Annotated[Union[SimpleStep, RepeatStep], Field(discriminator="type")]


def _end_condition_dict(ec: EndCondition) -> tuple[dict, float | None]:
    cid, ckey = _CONDITION_TYPE[ec.type]
    base = {"conditionTypeId": cid, "conditionTypeKey": ckey, "displayOrder": cid, "displayable": True}
    value_map = {
        "time":             lambda e: e.duree_minutes * 60,
        "distance":         lambda e: e.distance_km * 1000,
        "calories":         lambda e: float(e.calories),
        "power":            lambda e: float(e.watts),
        "heart_rate":       lambda e: float(e.bpm),
        "lap_button":       lambda e: None,
        "iterations":       lambda e: float(e.count),
        "fixed_rest":       lambda e: e.duree_minutes * 60,
        "fixed_repetition": lambda e: float(e.count),
        "reps":             lambda e: float(e.count),
    }
    return base, value_map[ec.type](ec)


def _estimated_seconds(s: SimpleStep) -> int:
    ec = s.end_condition
    if ec.type == "time":
        return int(ec.duree_minutes * 60)
    if ec.type == "fixed_rest":
        return int(ec.duree_minutes * 60)
    if ec.type == "distance" and isinstance(s.target, TargetPace):
        allure_moy = (s.target.allure_rapide_min_par_km + s.target.allure_lente_min_par_km) / 2  # min/km
        return int(ec.distance_km * allure_moy * 60)
    return 0


def _build_steps(steps: list[WorkoutStep], start_order: int = 1):
    from garminconnect.workout import ExecutableStep, create_repeat_group

    built = []
    order = start_order
    for s in steps:
        if s.type == "repeat":
            inner, _ = _build_steps(s.steps, start_order=1)
            built.append(create_repeat_group(s.repetitions, inner, order))
        else:
            end_cond, end_val = _end_condition_dict(s.end_condition)
            target_type, v1, v2 = _target_fields(s.target) if s.target else (None, None, None)
            built.append(ExecutableStep(
                stepOrder=order,
                stepType=_STEP_TYPE[s.type],
                endCondition=end_cond,
                endConditionValue=end_val,
                targetType=target_type,
                targetValueOne=v1,
                targetValueTwo=v2,
            ))
        order += 1
    return built, order


def _total_duration(steps: list[WorkoutStep]) -> int:
    total = 0
    for s in steps:
        if s.type == "repeat":
            total += _total_duration(s.steps) * s.repetitions
        else:
            total += _estimated_seconds(s)
    return total


def _running_workout(name: str, steps: list[WorkoutStep]):
    try:
        from garminconnect.workout import RunningWorkout, WorkoutSegment
    except ImportError:
        raise UnofficialError("garminconnect is not available on this server") from None

    workout_steps, _ = _build_steps(steps)
    segment = WorkoutSegment(
        segmentOrder=1,
        sportType={"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
        workoutSteps=workout_steps,
    )
    return RunningWorkout(
        workoutName=name,
        estimatedDurationInSecs=_total_duration(steps),
        workoutSegments=[segment],
    )


# ── Tools ─────────────────────────────────────────────────────────────────────


async def push_workout(date: str, name: str, steps: list[WorkoutStep]) -> dict:
    """
    Push a structured running workout to Garmin Connect and schedule it.
    Args:
        date: Target date in YYYY-MM-DD format (e.g., "2026-06-27")
        name: Workout name displayed on the watch
        steps: Ordered list of workout steps. Use SimpleStep for warmup/interval/recovery/cooldown,
               and RepeatStep to loop a block of SimpleSteps N times.
    Returns:
        dict with workout_id, date, and scheduled status
    """
    workout = _running_workout(name, steps)

    def upload_and_schedule(garmin) -> dict:
        result = garmin.upload_running_workout(workout)
        workout_id = result.get("workoutId") or result.get("detailId")
        if not workout_id:
            raise UnofficialError("Upload succeeded but Garmin Connect returned no workout id; nothing was scheduled")
        garmin.schedule_workout(workout_id, date)
        return {"workout_id": workout_id, "date": date, "scheduled": True}

    return await unofficial.client.call(upload_and_schedule)


async def list_workouts(limit: int = 50) -> list:
    """
    List saved workouts from Garmin Connect.
    Args:
        limit: Maximum number of workouts to return (default: 50)
    Returns:
        List of workouts with their id, name, sport type and duration
    """
    return await unofficial.client.call(lambda garmin: garmin.get_workouts(0, limit))


async def get_workout(workout_id: int) -> dict:
    """
    Get the full detail of a saved workout by its id.
    Args:
        workout_id: Workout id as returned by list_workouts
    Returns:
        Full workout structure including all steps, targets and conditions
    """
    return await unofficial.client.call(lambda garmin: garmin.get_workout_by_id(workout_id))


async def schedule_existing_workout(workout_id: int, date: str) -> dict:
    """
    Schedule an existing saved workout on a specific date.
    Args:
        workout_id: Workout id as returned by list_workouts
        date: Target date in YYYY-MM-DD format
    Returns:
        dict with workout_id, date and scheduled status
    """
    await unofficial.client.call(lambda garmin: garmin.schedule_workout(workout_id, date))
    return {"workout_id": workout_id, "date": date, "scheduled": True}


async def move_scheduled_workout(scheduled_id: int, new_date: str) -> dict:
    """
    Move an already-scheduled workout to a different date.
    Args:
        scheduled_id: ID of the calendar entry as returned by get_scheduled_workouts
        new_date: New date in YYYY-MM-DD format
    Returns:
        dict with workout_id, old scheduled_id and new date
    """

    def move(garmin) -> dict:
        scheduled = garmin.get_scheduled_workout_by_id(scheduled_id)
        workout_id = scheduled.get("workoutId")
        if not workout_id:
            # Checked before unscheduling, so the calendar entry is never lost.
            raise UnofficialError(f"Calendar entry {scheduled_id} has no workout id; nothing was moved")
        garmin.unschedule_workout(scheduled_id)
        garmin.schedule_workout(workout_id, new_date)
        return {"workout_id": workout_id, "scheduled_id": scheduled_id, "new_date": new_date}

    return await unofficial.client.call(move)


async def get_scheduled_workouts(year: int, month: int) -> dict:
    """
    Get workouts scheduled on the Garmin Connect calendar for a given month.
    Args:
        year: Year (e.g., 2026)
        month: Month as integer 1-12
    Returns:
        Calendar of the month; calendarItems lists the scheduled workouts with date and workout details
    """
    return await unofficial.client.call(lambda garmin: garmin.get_scheduled_workouts(year, month))


_TOOLS = (
    push_workout,
    list_workouts,
    get_workout,
    schedule_existing_workout,
    move_scheduled_workout,
    get_scheduled_workouts,
)


def register(mcp: FastMCP) -> None:
    """Register the workout tools on the given FastMCP instance, in declaration order."""
    for fn in _TOOLS:
        mcp.tool()(fn)
