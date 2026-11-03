from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=3, max_length=160)
    parcel_id: str = Field(pattern=r"^RC-\d{3}$")
    work_type: Literal[
        "repair", "extension", "groundworks", "new_structure", "demolition"
    ]
    start_date: date
    end_date: date
    height_m: float = Field(ge=0, le=200)
    footprint_m2: float = Field(ge=0, le=10000)
    materials: str = Field(min_length=2, max_length=1000)
    colour: str = Field(min_length=2, max_length=500)
    intended_use: str = Field(min_length=2, max_length=1000)
    narrative: str = Field(min_length=20, max_length=12000)
    drainage_changes: bool = False


class Citation(BaseModel):
    document_id: str
    version: str = "1"
    section: str


class Reason(BaseModel):
    explanation: str = Field(min_length=10, max_length=3000)
    policy_citations: list[Citation] = Field(min_length=1, max_length=15)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: str
    decision: Literal["approve", "deny"]
    summary: str = Field(min_length=10, max_length=1000)
    reasons: list[Reason] = Field(min_length=1, max_length=12)
    policy_version: str
    termination_reason: Literal["early_decision", "iteration_limit"] = "early_decision"
    decision_origin: Literal["model", "iteration_limit_fallback"] = "model"
