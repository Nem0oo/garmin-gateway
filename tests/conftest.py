import os

# config.py reads these at import time, so they must be set before any garmin_gateway import.
# Assigned (not setdefault) so values exported in the developer's shell can't change the tests.
os.environ.update(
    MCP_API_KEY="k" * 32,
    REST_API_KEY="r" * 32,
    GARMIN_PERSONAL_API_KEY="garmin-personal-key",
    DOMAIN="mcp.example.com",
    MAX_FILE_BYTES="1000",
    HTTP_TIMEOUT="30",
    GARMIN_BASE_URL="https://garmin.test",
    GARMIN_TOKEN_STORE="/nonexistent/garmin_tokens.json",
)

import pytest  # noqa: E402

API_KEY = os.environ["MCP_API_KEY"]
REST_KEY = os.environ["REST_API_KEY"]
DOMAIN = os.environ["DOMAIN"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def http():
    """Client on the full app, lifespan included (needed by /mcp)."""
    # The MCP session manager can only be started once per process, so the app's lifespan is shared.
    from starlette.testclient import TestClient

    from garmin_gateway.app import app

    with TestClient(app, base_url=f"https://{DOMAIN}") as c:
        yield c
