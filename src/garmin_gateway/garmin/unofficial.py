"""Unofficial Garmin Connect client (garminconnect) used by the workout tools.

garminconnect is imported on the first call only: the service starts, and the read tools work,
without it and without a token file.
"""

import os
import threading
from collections.abc import Callable
from typing import Any, TypeVar

import anyio

from .. import config

T = TypeVar("T")

LOGIN_HINT = "Run: docker exec -it <container> python -m garmin_gateway.login"


class UnofficialError(Exception):
    """Garmin Connect failure, with a message safe to show to the MCP client."""


class UnofficialClient:
    """Lazily created garminconnect session, shared by the workout tools.

    The logged-in Garmin object is cached: logging in from the token file costs a request to Garmin,
    and garminconnect refreshes the tokens (and rewrites the file) by itself. An authentication error
    drops the cached object, so the next call reloads the token file, e.g. after a new login.
    """

    def __init__(self) -> None:
        self._garmin: Any = None
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._garmin = None

    async def call(self, fn: Callable[[Any], T]) -> T:
        """Run fn(garmin) in a worker thread (garminconnect is blocking)."""
        return await anyio.to_thread.run_sync(self._run, fn)

    def _run(self, fn: Callable[[Any], T]) -> T:
        try:
            from garminconnect import exceptions as gc
        except ImportError:
            raise UnofficialError("garminconnect is not available on this server") from None

        try:
            return fn(self._session())
        except UnofficialError:
            raise
        except gc.GarminConnectAuthenticationError:
            self.reset()
            raise UnofficialError(f"Garmin Connect rejected the saved tokens. {LOGIN_HINT}") from None
        except gc.GarminConnectTooManyRequestsError:
            raise UnofficialError("Garmin Connect rate limit reached, retry later") from None
        except gc.GarminConnectNotFoundError:
            raise UnofficialError("Garmin Connect resource not found") from None
        except gc.GarminConnectConnectionError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f" (HTTP {status})" if status else ""
            raise UnofficialError(f"Garmin Connect request failed{suffix}") from None
        except ValueError as exc:
            # garminconnect validates ids, dates and months with ValueError and plain messages.
            raise UnofficialError(f"Invalid request: {exc}") from None
        except Exception as exc:
            # Other messages may carry URLs or response bodies: only the type is shown.
            raise UnofficialError(f"Garmin Connect request failed ({type(exc).__name__})") from None

    def _session(self) -> Any:
        with self._lock:
            if self._garmin is None:
                self._garmin = self._login()
            return self._garmin

    @staticmethod
    def _login() -> Any:
        from garminconnect import Garmin

        path = os.path.expanduser(config.GARMIN_TOKEN_STORE)
        if not os.path.isfile(path):
            raise UnofficialError(f"No Garmin Connect tokens at {path}. {LOGIN_HINT}") from None
        garmin = Garmin()
        garmin.login(path)
        return garmin


client = UnofficialClient()
