"""Exercise the configured model + CPU retrieval against an in-process API.

Run from repo root: backend/.venv/bin/python scripts/smoke_review.py
This verifies business integration; it does not prove OpenShell enforcement.
"""

import os
import tempfile
import uuid
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///" + str(
    Path(tempfile.mkdtemp(prefix="red-compass-smoke-")) / "smoke.db"
)
from anthropic import Anthropic
from fastapi.testclient import TestClient
from red_compass.api import app
from red_compass import auth, retrieval
from red_compass.agent import run, Tools
from red_compass.config import model_endpoint, settings
from red_compass.db import Session, Ticket

retrieval.ingest()
s = settings()
model_endpoint(s)
model = Anthropic(api_key=s.anthropic_api_key, base_url=s.anthropic_base_url, max_retries=1)
cases = [
    (
        "lantern",
        "RC-101",
        "approve",
        "Unchanged public evening lantern",
        "Repair the protected public evening lantern with hidden LED internals only. Preserve original visible stone and metal, outward form, height and footprint. No joinery changes, no excavation and no drainage changes. Historic public evening-light function stays the same.",
    ),
    (
        "stair",
        "RC-104",
        "deny",
        "Everyday commuter access",
        "Repair the existing ceremonial stair in matching stone at unchanged dimensions. Reopen it for everyday commuter access rather than ceremonial occasions. No excavation, footprint changes or drainage changes.",
    ),
]
with TestClient(app) as http:
    http.post("/api/session", json={"customer": "alex"}).raise_for_status()
    for name, parcel, expected, use, narrative in cases:
        response = http.post(
            "/api/requests",
            json={
                "title": f"Smoke: {name}",
                "parcel_id": parcel,
                "work_type": "repair",
                "start_date": "2027-07-12",
                "end_date": "2027-07-16",
                "height_m": 2,
                "footprint_m2": 12 if name == "lantern" else 24,
                "materials": "Preserved original visible stone and metal",
                "colour": "Unchanged stone grey",
                "intended_use": use,
                "narrative": narrative,
            },
        )
        response.raise_for_status()
        ticket = response.json()["id"]
        attempt = str(uuid.uuid4())
        with Session.begin() as db:
            db.get(Ticket, ticket).active_attempt = attempt
        http.headers["Authorization"] = "Bearer " + auth.issue(
            ticket, "agent", attempt_id=attempt
        )
        tools = Tools(
            "http://testserver", "", ticket, attempt, s.policy_version, client=http
        )
        result = run(model, tools, timeout=300)
        stored = http.get(f"/api/requests/{ticket}").json()
        actual = stored["decision"]["decision"]
        print(
            {"case": name, "expected": expected, "actual": actual, **result}, flush=True
        )
        assert actual == expected, stored["decision"]["summary"]
