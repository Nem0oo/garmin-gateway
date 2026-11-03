import os


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} environment variable is required")
    return value


def _api_key(name: str) -> str:
    value = _required(name)
    if len(value) < 32:
        raise RuntimeError(f"{name} must be at least 32 characters")
    return value


# Key protecting /mcp (what claude.ai / MCP clients send).
MCP_API_KEY = _api_key("MCP_API_KEY")
# Key protecting the read-only REST API under /api/.
REST_API_KEY = _api_key("REST_API_KEY")
# Each key opens exactly one entry point, so a shared value would merge them.
if MCP_API_KEY == REST_API_KEY:
    raise RuntimeError("MCP_API_KEY and REST_API_KEY must be different")

# Personal API key generated on the Garmin Connect web account. Exchanged for a short-lived access token.
GARMIN_PERSONAL_API_KEY = _required("GARMIN_PERSONAL_API_KEY")

# Public hostname served by nginx-proxy (Host header validation, DNS rebinding protection).
DOMAIN = _required("DOMAIN")

GARMIN_BASE_URL = os.environ.get("GARMIN_BASE_URL", "https://personal.connectapi.garmin.com").rstrip("/")
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"

PORT = int(os.environ.get("PORT", "8001"))
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "30"))
# Cap on the binary activity file returned through MCP (bytes, before base64).
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", str(5 * 1024 * 1024)))
# Cap on the zipped activity file downloaded for the REST API (bytes).
MAX_FIT_BYTES = int(os.environ.get("MAX_FIT_BYTES", str(20 * 1024 * 1024)))

# garminconnect token file used by the workout tools; it may not exist yet when the service starts.
GARMIN_TOKEN_STORE = os.environ.get("GARMIN_TOKEN_STORE", "/data/garmin_tokens.json")
