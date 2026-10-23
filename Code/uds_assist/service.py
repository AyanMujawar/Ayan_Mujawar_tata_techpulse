"""Service layer shared by the FastAPI backend and the Streamlit UI."""
from __future__ import annotations

import json
from collections import Counter

from . import exporter, llm, qa, store
from .config import ecu_extract, load_config, path, requirements
from .generator import applicable_categories, generate
from .rules import check_pair, validate_request


def ask(question: str, collections: list[str] | None = None) -> dict:
    result = qa.answer(question, collections)
    store.log_qa(result)
    return result


def validate(request_hex: str, session: str = "default", unlocked: list[int] | None = None,
             seed_requested_level: int | None = None, response_hex: str | None = None) -> dict:
    if response_hex:
        return check_pair(request_hex, response_hex, session, unlocked, seed_requested_level)
    return validate_request(request_hex, session, unlocked, seed_requested_level)


def generate_tests(requirement_id: str, use_llm: bool = True) -> dict:
    result = generate(requirement_id, use_llm=use_llm)
    result["store"] = store.save_drafts(result["cases"])
    return result


def review(test_id: str, decision: str, reviewer: str, comment: str = "", edits: dict | None = None) -> dict:
    return store.review(test_id, decision, reviewer, comment, edits)


def export(fmt: str = "both", requirement_id: str | None = None, actor: str = "engineer") -> dict:
    return exporter.export(fmt, requirement_id, actor)


def existing_tests() -> list[dict]:
    with open(path("input_data") / "project_reqs" / "existing_test_cases.json", encoding="utf-8") as fh:
        return json.load(fh)["tests"]


def coverage() -> dict:
    """Per requirement: applicable categories vs categories covered by reviewed (existing or APPROVED) tests."""
    existing = existing_tests()
    approved = store.list_tests(status="APPROVED")
    drafts = store.list_tests(status="DRAFT")
    rows = []
    for rid, req in requirements().items():
        need = applicable_categories(req)
        have_existing = {t["category"] for t in existing if t["requirement"] == rid}
        have_approved = {t["category"] for t in approved if t["requirement_id"] == rid}
        have_draft = {t["category"] for t in drafts if t["requirement_id"] == rid}
        covered = (have_existing | have_approved) & set(need)
        rows.append({"requirement": rid, "title": req["title"], "service": req["service"], "applicable": need,
                     "covered_existing": sorted(have_existing & set(need)), "covered_approved": sorted(have_approved & set(need)),
                     "pending_draft": sorted((have_draft - covered) & set(need)),
                     "missing": [c for c in need if c not in covered],
                     "coverage_pct": round(100 * len(covered) / len(need), 1) if need else 100.0,
                     "has_negative_test": any(c != "positive" for c in covered)})
    total_need = sum(len(r["applicable"]) for r in rows)
    total_cov = sum(len(r["applicable"]) - len(r["missing"]) for r in rows)
    return {"rows": rows, "overall_pct": round(100 * total_cov / total_need, 1),
            "requirements_without_negative_test": sum(not r["has_negative_test"] for r in rows),
            "requirements": len(rows), "applicable_scenarios": total_need, "covered_scenarios": total_cov}


def kb_stats() -> dict:
    """Simple EDA of the knowledge base."""
    ex = ecu_extract()
    reqs = requirements().values()
    meta_file = path("index_dir") / "index_meta.json"
    return {
        "index": json.loads(meta_file.read_text()) if meta_file.exists() else None,
        "services_per_requirement": dict(Counter(r["service"] for r in reqs)),
        "dids_by_access": dict(Counter(d["access"] for d in ex["dids"])),
        "dids_by_write_security": dict(Counter(str(d.get("write_security")) for d in ex["dids"] if d["access"] == "RW")),
        "dids_readable_in_default": sum("default" in d["read_sessions"] for d in ex["dids"]),
        "dids": len(ex["dids"]), "rids": len(ex["rids"]), "requirements": len(requirements()),
        "existing_tests_by_category": dict(Counter(t["category"] for t in existing_tests())),
    }


def health() -> dict:
    return {"llm_model": load_config()["llm"]["model"], "llm_available": llm.available(),
            "index_built": (path("index_dir") / "index_meta.json").exists()}
