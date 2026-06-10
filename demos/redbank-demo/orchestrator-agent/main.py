"""Thin entrypoint for ``uvicorn main:app``.

All logic lives in :mod:`redbank_orchestrator.server`.
"""

from redbank_orchestrator.server import app  # noqa: F401

if __name__ == "__main__":
    from redbank_orchestrator.server import main

    main()
