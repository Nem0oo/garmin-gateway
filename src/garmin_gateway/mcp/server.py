import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ..config import DOMAIN, GARMIN_TOKEN_STORE
from . import read_tools, workout_tools

mcp = FastMCP(
    "garmin-gateway",
    instructions=(
        "Garmin data of the user. Activity, health and wellness data are read through the official "
        "Garmin Personal API. Running workouts are written through Garmin Connect: push_workout creates "
        "and schedules one; list_workouts, get_workout, schedule_existing_workout, move_scheduled_workout "
        "and get_scheduled_workouts manage the saved workouts and the calendar. server_health tells whether "
        "the Garmin Connect tokens are present; if a workout tool reports missing or rejected tokens, the "
        "owner must run `python -m garmin_gateway.login` in the container. "
        "Dates are YYYY-MM-DD. Prefer date-range tools over many single-day calls."
    ),
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[DOMAIN, f"{DOMAIN}:*"],
        allowed_origins=[f"https://{DOMAIN}"],
    ),
)


@mcp.tool()
def server_health() -> dict[str, object]:
    """Check that the server is up and whether the workout write tokens are present (Garmin is not called)."""
    return {"status": "ok", "write_tokens": os.path.isfile(GARMIN_TOKEN_STORE)}


read_tools.register(mcp)
workout_tools.register(mcp)
