import runpy
from pathlib import Path

import pytest

import garmin_gateway.config

CONFIG_PATH = Path(garmin_gateway.config.__file__)

VALID_ENV = {
    "MCP_API_KEY": "m" * 32,
    "REST_API_KEY": "r" * 32,
    "GARMIN_PERSONAL_API_KEY": "garmin-personal-key",
    "DOMAIN": "gw.example.com",
}
OPTIONAL = ["GARMIN_TOKEN_STORE", "MAX_FIT_BYTES", "MAX_FILE_BYTES", "PORT", "HTTP_TIMEOUT", "GARMIN_BASE_URL"]


@pytest.fixture
def load(monkeypatch):
    """Evaluate config.py in isolation with the given environment (the imported module stays untouched)."""

    def _load(**overrides):
        for name in [*VALID_ENV, *OPTIONAL]:
            monkeypatch.delenv(name, raising=False)
        for name, value in {**VALID_ENV, **overrides}.items():
            if value is not None:
                monkeypatch.setenv(name, value)
        return runpy.run_path(str(CONFIG_PATH))

    return _load


def test_valid_environment_loads(load):
    cfg = load()
    assert cfg["MCP_API_KEY"] == "m" * 32
    assert cfg["REST_API_KEY"] == "r" * 32


@pytest.mark.parametrize("name", list(VALID_ENV))
def test_required_variables(load, name):
    with pytest.raises(RuntimeError, match=name):
        load(**{name: None})


@pytest.mark.parametrize("name", ["MCP_API_KEY", "REST_API_KEY"])
def test_keys_must_be_at_least_32_characters(load, name):
    with pytest.raises(RuntimeError, match=f"{name} must be at least 32 characters"):
        load(**{name: "x" * 31})


def test_keys_must_differ(load):
    with pytest.raises(RuntimeError, match="must be different"):
        load(MCP_API_KEY="s" * 40, REST_API_KEY="s" * 40)


def test_keys_are_stripped_before_comparison(load):
    with pytest.raises(RuntimeError, match="must be different"):
        load(MCP_API_KEY="s" * 40, REST_API_KEY=" " + "s" * 40 + " ")


def test_defaults(load):
    cfg = load()
    assert cfg["GARMIN_TOKEN_STORE"] == "/data/garmin_tokens.json"
    assert cfg["MAX_FIT_BYTES"] == 20 * 1024 * 1024
    assert cfg["MAX_FILE_BYTES"] == 5 * 1024 * 1024
    assert cfg["PORT"] == 8001


def test_overrides(load):
    cfg = load(GARMIN_TOKEN_STORE="/tmp/tokens.json", MAX_FIT_BYTES="1234")
    assert cfg["GARMIN_TOKEN_STORE"] == "/tmp/tokens.json"
    assert cfg["MAX_FIT_BYTES"] == 1234
