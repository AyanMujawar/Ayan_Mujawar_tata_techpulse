"""SQLite persistence: test cases with review status (DRAFT -> APPROVED / REJECTED),
Q&A log and an append-only audit log."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from .config import path

SCHEMA = """
CREATE TABLE IF NOT EXISTS test_cases (
  id TEXT PRIMARY KEY, requirement_id TEXT, category TEXT, status TEXT, source TEXT,
  data TEXT, created_at TEXT, updated_at TEXT, reviewer TEXT, review_comment TEXT);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, action TEXT, target TEXT, details TEXT);
CREATE TABLE IF NOT EXISTS qa_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, question TEXT, answer TEXT, citations TEXT,
  source TEXT, model TEXT, prompt_version TEXT, latency_s REAL);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def db():
    con = sqlite3.connect(path("database"))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def audit(action: str, target: str, details: dict | None = None, actor: str = "system"):
    with db() as con:
        con.execute("INSERT INTO audit_log (ts, actor, action, target, details) VALUES (?,?,?,?,?)",
                    (_now(), actor, action, target, json.dumps(details or {})))


def save_drafts(cases: list[dict], actor: str = "assistant") -> dict:
    """Stores generated cases as DRAFT. Already APPROVED/REJECTED cases are never overwritten."""
    saved, kept = [], []
    with db() as con:
        for c in cases:
            row = con.execute("SELECT status FROM test_cases WHERE id=?", (c["id"],)).fetchone()
            if row and row["status"] != "DRAFT":
                kept.append(c["id"])
                continue
            con.execute("INSERT OR REPLACE INTO test_cases (id, requirement_id, category, status, source, data, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?)", (c["id"], c["requirement_id"], c["category"], "DRAFT", c["source"],
                                                     json.dumps(c), _now(), _now()))
            saved.append(c["id"])
    audit("generate", ",".join(sorted({c["requirement_id"] for c in cases})), {"saved": saved, "kept_reviewed": kept}, actor)
    return {"saved": saved, "kept_reviewed": kept}


def list_tests(status: str | None = None, requirement_id: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM test_cases WHERE 1=1", []
    if status:
        q, args = q + " AND status=?", args + [status]
    if requirement_id:
        q, args = q + " AND requirement_id=?", args + [requirement_id]
    with db() as con:
        rows = con.execute(q + " ORDER BY id", args).fetchall()
    out = []
    for r in rows:
        d = json.loads(r["data"])
        d.update({"status": r["status"], "reviewer": r["reviewer"], "review_comment": r["review_comment"],
                  "reviewed_at": r["updated_at"]})
        out.append(d)
    return out


def review(test_id: str, decision: str, reviewer: str, comment: str = "", edits: dict | None = None) -> dict:
    """decision: approve | reject | reopen. Only a named engineer can approve."""
    status = {"approve": "APPROVED", "reject": "REJECTED", "reopen": "DRAFT"}[decision]
    if not reviewer.strip():
        raise ValueError("a reviewer name is required")
    with db() as con:
        row = con.execute("SELECT data FROM test_cases WHERE id=?", (test_id,)).fetchone()
        if not row:
            raise KeyError(test_id)
        data = json.loads(row["data"])
        for k in ("title", "preconditions", "pass_criteria"):
            if edits and k in edits:
                data[k] = edits[k]
        data["status"] = status
        con.execute("UPDATE test_cases SET status=?, reviewer=?, review_comment=?, updated_at=?, data=? WHERE id=?",
                    (status, reviewer, comment, _now(), json.dumps(data), test_id))
    audit(f"review:{decision}", test_id, {"comment": comment, "edited": sorted((edits or {}).keys())}, reviewer)
    return {"id": test_id, "status": status}


def log_qa(result: dict):
    with db() as con:
        con.execute("INSERT INTO qa_log (ts, question, answer, citations, source, model, prompt_version, latency_s) VALUES (?,?,?,?,?,?,?,?)",
                    (_now(), result["question"], result["answer"], json.dumps(result["citations"]), result["source"],
                     result.get("model"), result.get("prompt_version"), result.get("latency_s")))


def audit_entries(limit: int = 50) -> list[dict]:
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
