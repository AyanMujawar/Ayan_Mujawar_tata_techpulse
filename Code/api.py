"""FastAPI backend (localhost only).

Run:  uvicorn api:app --host 127.0.0.1 --port 8000      ->  docs at http://127.0.0.1:8000/docs
"""
from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from uds_assist import service, store

app = FastAPI(title="UDS Diagnostics & Test Generation Assistant", version="1.0.0",
              description="CS5 - cited UDS Q&A, deterministic request validation, test generation with engineer review.")


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    collections: list[Literal["standard_uds", "oem_spec", "ecu_extract", "project_reqs"]] | None = None


class ValidateIn(BaseModel):
    request: str = Field(examples=["2E 4A 10 00 3C"])
    session: Literal["default", "extended", "programming"] = "default"
    unlocked: list[Literal[1, 3]] = []
    seed_requested_level: Literal[1, 3] | None = None
    response: str | None = Field(default=None, description="optional response to judge")


class GenerateIn(BaseModel):
    requirement_id: str = Field(pattern=r"^REQ-BCM-\d{3}$", examples=["REQ-BCM-021"])
    use_llm: bool = True


class ReviewIn(BaseModel):
    test_id: str
    decision: Literal["approve", "reject", "reopen"]
    reviewer: str = Field(min_length=2)
    comment: str = ""


class ExportIn(BaseModel):
    format: Literal["python", "capl", "both"] = "both"
    requirement_id: str | None = None
    actor: str = "engineer"


@app.get("/health")
def health():
    return service.health()


@app.post("/ask")
def ask(body: AskIn):
    return service.ask(body.question, body.collections)


@app.post("/validate-request")
def validate(body: ValidateIn):
    try:
        return service.validate(body.request, body.session, body.unlocked, body.seed_requested_level, body.response)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/generate-tests")
def generate_tests(body: GenerateIn):
    try:
        return service.generate_tests(body.requirement_id, body.use_llm)
    except KeyError as exc:
        raise HTTPException(404, f"unknown requirement {exc}") from exc


@app.get("/tests")
def tests(status: str | None = None, requirement_id: str | None = None):
    return store.list_tests(status, requirement_id)


@app.post("/review")
def review(body: ReviewIn):
    try:
        return service.review(body.test_id, body.decision, body.reviewer, body.comment)
    except KeyError as exc:
        raise HTTPException(404, f"unknown test {exc}") from exc


@app.post("/export")
def export(body: ExportIn):
    return service.export(body.format, body.requirement_id, body.actor)


@app.get("/coverage")
def coverage():
    return service.coverage()


@app.get("/audit")
def audit(limit: int = 50):
    return store.audit_entries(limit)
