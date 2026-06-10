"""Thin entrypoint for uvicorn — imports the ASGI app from server.py."""

from server import app  # noqa: F401
