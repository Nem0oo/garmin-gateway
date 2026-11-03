"""Stand-in for garminconnect.Garmin: records every call, never touches the network."""

import threading

import pytest

from garmin_gateway import config
from garmin_gateway.garmin import unofficial


class FakeGarmin:
    instances: list["FakeGarmin"] = []
    # Exception raised by login(), or None for a successful login.
    login_error: Exception | None = None

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.logins = []
        self.calls = []
        self.uploaded = []
        self.threads = []
        FakeGarmin.instances.append(self)

    def login(self, tokenstore=None):
        self.logins.append(tokenstore)
        if FakeGarmin.login_error is not None:
            raise FakeGarmin.login_error

    def _record(self, name, *args):
        self.calls.append((name, *args))
        self.threads.append(threading.get_ident())

    def upload_running_workout(self, workout):
        self._record("upload_running_workout")
        self.uploaded.append(workout)
        return {"workoutId": 123}

    def schedule_workout(self, workout_id, date):
        self._record("schedule_workout", workout_id, date)
        return {}

    def unschedule_workout(self, scheduled_id):
        self._record("unschedule_workout", scheduled_id)

    def get_workouts(self, start, limit):
        self._record("get_workouts", start, limit)
        return [{"workoutId": 1, "workoutName": "Easy"}]

    def get_workout_by_id(self, workout_id):
        self._record("get_workout_by_id", workout_id)
        return {"workoutId": workout_id}

    def get_scheduled_workout_by_id(self, scheduled_id):
        self._record("get_scheduled_workout_by_id", scheduled_id)
        return {"workoutId": 77, "date": "2026-11-05"}

    def get_scheduled_workouts(self, year, month):
        self._record("get_scheduled_workouts", year, month)
        return {"calendarItems": [{"id": 9, "date": f"{year}-{month:02d}-05"}]}


@pytest.fixture
def fake_garmin(tmp_path, monkeypatch):
    """garminconnect.Garmin replaced by FakeGarmin, a token file present, and no cached client."""
    store = tmp_path / "garmin_tokens.json"
    store.write_text("{}")
    monkeypatch.setattr(config, "GARMIN_TOKEN_STORE", str(store))
    monkeypatch.setattr("garminconnect.Garmin", FakeGarmin)
    monkeypatch.setattr(FakeGarmin, "instances", [])
    monkeypatch.setattr(FakeGarmin, "login_error", None)
    unofficial.client.reset()
    yield FakeGarmin
    unofficial.client.reset()
