"""
RedBank Playground — Standalone UI for the RedBank Orchestrator agent.

Lightweight Starlette app that:
  - Serves the playground HTML + static images
  - Handles Keycloak OIDC auth directly (no orchestrator involvement)
  - Proxies /chat/completions to the orchestrator
  - Proxies /health to the orchestrator for agent status
"""

from __future__ import annotations

import logging
from os import getenv
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

load_dotenv()

_log_level = getattr(logging, getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(level=_log_level)
logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent
_CONTAINER_ROOT = Path("/opt/app-root/src")


def _find_path(*candidates: Path) -> Path:
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


_PLAYGROUND_HTML = _find_path(
    _CONTAINER_ROOT / "playground" / "templates" / "index.html",
    _PROJECT_ROOT / "playground" / "templates" / "index.html",
)
_IMAGES_DIR = _find_path(
    _CONTAINER_ROOT / "images",
    _PROJECT_ROOT / "images",
)

# ── Orchestrator backend ─────────────────────────────────────────────────────

_ORCHESTRATOR_URL = getenv("ORCHESTRATOR_URL", "http://localhost:8000").rstrip("/")


def _listen_port() -> int:
    return int(getenv("PORT", "8080"))


# ── Route handlers ───────────────────────────────────────────────────────────


async def _playground_page(_request: Request) -> FileResponse:
    if not _PLAYGROUND_HTML.is_file():
        raise HTTPException(status_code=404, detail="Playground template missing.")
    return FileResponse(_PLAYGROUND_HTML)


async def _serve_image(request: Request) -> FileResponse:
    filename = request.path_params["filename"]
    base = _IMAGES_DIR.resolve()
    file_path = (base / filename).resolve()
    try:
        file_path.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found") from None
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(file_path)


async def _health(_request: Request) -> JSONResponse:
    """Proxy health check to the orchestrator to reflect actual agent status."""
    try:
        async with httpx.AsyncClient(verify=False) as client:  # noqa: S501
            resp = await client.get(f"{_ORCHESTRATOR_URL}/health", timeout=5)
        return JSONResponse(resp.json(), status_code=resp.status_code)
    except Exception:
        logger.warning("Orchestrator health check unreachable: %s", _ORCHESTRATOR_URL)
        return JSONResponse(
            {"status": "unhealthy", "agent_initialized": False}, status_code=503
        )


async def _auth_config(_request: Request) -> JSONResponse:
    """Return Keycloak OIDC configuration for the playground UI.

    When all three KEYCLOAK_* env vars are set the response includes
    ``enabled: true`` and the values the frontend needs to perform the
    OAuth2 Authorization Code flow.  Otherwise ``enabled: false`` is
    returned and the playground operates without authentication.
    """
    kc_url = getenv("KEYCLOAK_URL", "").rstrip("/")
    kc_realm = getenv("KEYCLOAK_REALM", "")
    kc_client = getenv("KEYCLOAK_CLIENT_ID", "")

    if kc_url and kc_realm and kc_client:
        return JSONResponse(
            {
                "enabled": True,
                "url": kc_url,
                "realm": kc_realm,
                "clientId": kc_client,
            }
        )
    return JSONResponse({"enabled": False})


async def _auth_token(request: Request) -> JSONResponse:
    """Server-side proxy for Keycloak token requests.

    Accepts ``grant_type=authorization_code`` (with ``code`` + ``redirect_uri``)
    or ``grant_type=refresh_token`` (with ``refresh_token``).

    The browser never talks to Keycloak directly, avoiding all CORS issues.
    """
    kc_url = getenv("KEYCLOAK_URL", "").rstrip("/")
    kc_realm = getenv("KEYCLOAK_REALM", "")
    kc_client = getenv("KEYCLOAK_CLIENT_ID", "")

    if not (kc_url and kc_realm and kc_client):
        raise HTTPException(status_code=404, detail="Auth not configured")

    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

    grant_type = body.get("grant_type")
    if grant_type not in ("authorization_code", "refresh_token"):
        raise HTTPException(status_code=400, detail="Unsupported grant_type")

    token_url = f"{kc_url}/realms/{kc_realm}/protocol/openid-connect/token"
    form_data: dict[str, str] = {
        "client_id": kc_client,
        "grant_type": grant_type,
    }

    if grant_type == "authorization_code":
        code = body.get("code")
        redirect_uri = body.get("redirect_uri")
        code_verifier = body.get("code_verifier")
        if not code or not redirect_uri:
            raise HTTPException(
                status_code=400, detail="code and redirect_uri required"
            )
        form_data["code"] = code
        form_data["redirect_uri"] = redirect_uri
        if code_verifier:
            form_data["code_verifier"] = code_verifier
    else:
        rt = body.get("refresh_token")
        if not rt:
            raise HTTPException(status_code=400, detail="refresh_token required")
        form_data["refresh_token"] = rt

    try:
        async with httpx.AsyncClient(verify=False) as client:  # noqa: S501
            resp = await client.post(
                token_url,
                data=form_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("Keycloak token request failed")
        raise HTTPException(status_code=502, detail=f"Keycloak unreachable: {e}") from e

    return JSONResponse(resp.json(), status_code=resp.status_code)


async def _proxy_chat_completions(request: Request) -> Response:
    """Proxy POST /chat/completions to the orchestrator, with SSE streaming support."""
    body = await request.body()
    headers = dict(request.headers)
    # Forward Authorization header if present
    fwd_headers: dict[str, str] = {"Content-Type": "application/json"}
    if "authorization" in headers:
        fwd_headers["Authorization"] = headers["authorization"]

    import json

    try:
        parsed = json.loads(body)
    except Exception:
        parsed = {}

    stream = parsed.get("stream", False)

    if stream:
        # Streaming: forward SSE chunks
        async def _stream_proxy():
            try:
                async with httpx.AsyncClient(verify=False) as client:  # noqa: S501
                    async with client.stream(
                        "POST",
                        f"{_ORCHESTRATOR_URL}/chat/completions",
                        content=body,
                        headers=fwd_headers,
                        timeout=120,
                    ) as resp:
                        async for chunk in resp.aiter_bytes():
                            yield chunk
            except Exception:
                logger.exception("Stream proxy failed")
                yield b'data: {"error": {"message": "Proxy error"}}\n\n'
                yield b"data: [DONE]\n\n"

        return StreamingResponse(
            _stream_proxy(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Non-streaming: simple forward
    try:
        async with httpx.AsyncClient(verify=False) as client:  # noqa: S501
            resp = await client.post(
                f"{_ORCHESTRATOR_URL}/chat/completions",
                content=body,
                headers=fwd_headers,
                timeout=120,
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("Failed to proxy /chat/completions")
        raise HTTPException(status_code=502, detail=str(e)) from e
    return JSONResponse(resp.json(), status_code=resp.status_code)


# ── Build app ────────────────────────────────────────────────────────────────


def build_app() -> Starlette:
    routes = [
        Route("/", _playground_page, methods=["GET"]),
        Route("/health", _health, methods=["GET"]),
        Route("/auth/config", _auth_config, methods=["GET"]),
        Route("/auth/token", _auth_token, methods=["POST"]),
        Route("/chat/completions", _proxy_chat_completions, methods=["POST"]),
        Route("/images/{filename:path}", _serve_image, methods=["GET"]),
    ]
    return Starlette(routes=routes)


app = build_app()


def main() -> None:
    port = _listen_port()
    logger.info(
        "RedBank Playground listening on 0.0.0.0:%s; orchestrator=%s",
        port,
        _ORCHESTRATOR_URL,
    )
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
