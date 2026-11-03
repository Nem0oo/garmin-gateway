import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


class FakeTokens:
    def __init__(self):
        self.dumped = []

    def dump(self, path):
        self.dumped.append(path)
        Path(path).write_text("{}")


class FakeGarmin:
    instances = []

    def __init__(self, email=None, password=None, prompt_mfa=None):
        self.email, self.password, self.prompt_mfa = email, password, prompt_mfa
        self.logins = []
        self.client = FakeTokens()
        FakeGarmin.instances.append(self)

    def login(self, *args):
        self.logins.append(args)

    def get_full_name(self):
        return "Jane Runner"


@pytest.fixture
def fake_login(monkeypatch, tmp_path):
    from garmin_gateway import login

    store = tmp_path / "nested" / "garmin_tokens.json"
    monkeypatch.setenv("GARMIN_TOKEN_STORE", str(store))
    monkeypatch.setenv("GARMIN_EMAIL", "jane@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "hunter2-password")
    monkeypatch.setattr("garminconnect.Garmin", FakeGarmin)
    monkeypatch.setattr(FakeGarmin, "instances", [])
    return login, store


def test_login_saves_fresh_tokens_to_the_token_store(fake_login, capsys):
    login, store = fake_login
    login.main()

    (garmin,) = FakeGarmin.instances
    assert (garmin.email, garmin.password) == ("jane@example.com", "hunter2-password")
    assert callable(garmin.prompt_mfa)
    assert garmin.logins == [()]  # credentials only, saved tokens are ignored
    assert garmin.client.dumped == [str(store)]
    assert store.is_file()
    out = capsys.readouterr().out
    assert "Jane Runner" in out and str(store) in out
    assert "hunter2-password" not in out


def test_login_prompts_for_missing_credentials(fake_login, monkeypatch):
    login, _ = fake_login
    monkeypatch.delenv("GARMIN_EMAIL")
    monkeypatch.delenv("GARMIN_PASSWORD")
    monkeypatch.setattr("builtins.input", lambda prompt: "typed@example.com")
    monkeypatch.setattr("getpass.getpass", lambda prompt: "typed-password")
    login.main()
    (garmin,) = FakeGarmin.instances
    assert (garmin.email, garmin.password) == ("typed@example.com", "typed-password")


def test_login_default_token_store_matches_the_service():
    env = {k: v for k, v in os.environ.items() if k not in {"MCP_API_KEY", "REST_API_KEY", "GARMIN_PERSONAL_API_KEY", "DOMAIN", "GARMIN_TOKEN_STORE"}}
    env.update(PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
    # Imported without the service keys: the login script must not load config.py.
    r = subprocess.run(
        [sys.executable, "-c", "from garmin_gateway import login; print(login.token_store())"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "/data/garmin_tokens.json"
