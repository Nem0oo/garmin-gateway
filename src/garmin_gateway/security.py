import hmac

from starlette.responses import JSONResponse

from .config import MCP_API_KEY, REST_API_KEY

# Marker for paths served without a key.
OPEN = object()


def _required_key(path: str):
    """Key that opens `path`, OPEN for public paths, None for paths outside every known prefix."""
    if path == "/health":
        return OPEN
    if path == "/mcp" or path.startswith("/mcp/"):
        return MCP_API_KEY
    if path.startswith("/api/"):
        return REST_API_KEY
    return None


def _headers(scope) -> dict[bytes, bytes]:
    return {k.lower(): v for k, v in scope.get("headers") or []}


def _valid(scope, key: str) -> bool:
    # Only the Authorization header is accepted: a key in the URL ends up in proxy logs and history.
    # Compared as raw bytes in constant time, so any key (even non-ASCII, sent as UTF-8) matches exactly.
    auth = _headers(scope).get(b"authorization", b"")
    if auth[:7].lower() != b"bearer ":
        return False
    return hmac.compare_digest(auth[7:].strip(), key.encode())


class ApiKeyMiddleware:
    """Pure ASGI middleware (does not buffer streamed MCP responses).

    Each path prefix has its own key: a key never opens another prefix, and a path outside every
    prefix answers 404 without asking for a key, so its existence is never confirmed.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            # No websocket endpoint here: refuse anything else instead of passing it through unauthenticated.
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return

        key = _required_key(scope["path"])
        if key is OPEN:
            return await self.app(scope, receive, send)
        if key is None:
            resp = JSONResponse({"error": "Not found"}, status_code=404)
            return await resp(scope, receive, send)
        if not _valid(scope, key):
            resp = JSONResponse({"error": "Unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
            return await resp(scope, receive, send)
        return await self.app(scope, receive, send)
