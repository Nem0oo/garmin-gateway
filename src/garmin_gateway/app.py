import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .config import PORT
from .mcp.server import mcp
from .rest import routes as rest
from .security import ApiKeyMiddleware


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def build_app() -> Starlette:
    # The MCP app carries the /mcp route and the lifespan that runs its session manager.
    mcp_app = mcp.streamable_http_app()
    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Mount("/api/v1", routes=rest.routes),
            *mcp_app.routes,
        ],
        lifespan=mcp_app.router.lifespan_context,
    )
    app.add_middleware(ApiKeyMiddleware)
    return app


app = build_app()


def main() -> None:
    # access_log disabled: request details stay out of the logs.
    uvicorn.run(app, host="0.0.0.0", port=PORT, access_log=False, proxy_headers=False)
