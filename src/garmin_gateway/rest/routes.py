"""Read-only REST routes, mounted under /api/v1 by app.py.

Errors answer {"error": "<message>"} with messages that never carry credentials or traces.
"""
import re

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from ..config import MAX_FIT_BYTES
from ..garmin import fit
from ..garmin.fit import MAX_UNZIPPED_BYTES
from ..garmin.official import GarminError, GarminFileTooLarge, GarminNotFound
from ..mcp import read_tools

# Positive integer, no sign or leading zero; 19 digits stays within Garmin's 64-bit ids.
_ACTIVITY_ID = re.compile(r"[1-9][0-9]{0,18}")


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def activity_series(request: Request) -> JSONResponse:
    raw_id = request.path_params["activity_id"]
    if not _ACTIVITY_ID.fullmatch(raw_id):
        return _error(400, "activity_id must be a positive integer")
    activity_id = int(raw_id)

    # Same client as the MCP read tools: one access token and one connection pool per process.
    try:
        data = await read_tools.client.get_bytes(f"/download-service/files/activity/{activity_id}", max_bytes=MAX_FIT_BYTES)
    except GarminNotFound:
        return _error(404, "Activity not found (unknown or not synced yet)")
    except GarminFileTooLarge as exc:
        return _error(413, str(exc))
    except GarminError as exc:
        return _error(502, str(exc))

    # Decoding is CPU-bound: run it off the event loop.
    try:
        result = await anyio.to_thread.run_sync(fit.parse_activity_zip, data, MAX_UNZIPPED_BYTES)
    except fit.FitTooLarge as exc:
        return _error(413, str(exc))
    except fit.FitError as exc:
        return _error(422, str(exc))

    return JSONResponse({"activity_id": str(activity_id), **result})


# Starlette routes served under /api/v1 (paths here are relative to that prefix).
routes: list[BaseRoute] = [
    Route("/activities/{activity_id}/series", activity_series, methods=["GET"]),
]
