"""Cited diagnostic Q&A: hybrid retrieval -> identifier guard -> controlled prompt -> LLM JSON
-> citation guardrail. Falls back to an extractive answer if the local LLM is not running."""
from __future__ import annotations

import re
import time

from . import llm
from .config import ecu_extract, load_config, prompt
from .retrieval import Retriever
from .rules import encode_value, validate_request


def _known_ids() -> tuple[set[int], set[int]]:
    ex = ecu_extract()
    return {int(d["did"], 16) for d in ex["dids"]}, {int(r["rid"], 16) for r in ex["rids"]}


def find_identifiers(text: str) -> list[int]:
    """4-hex-digit identifiers written as 0xF190 or containing a hex letter (F190, 4A10)."""
    ids = []
    for m in re.finditer(r"\b(0x)?([0-9A-Fa-f]{4})\b", text):
        if m.group(1) or re.search(r"[A-Fa-f]", m.group(2)):
            ids.append(int(m.group(2), 16))
    return ids


def identifier_guard(question: str) -> dict:
    """Deterministic check: identifiers not in the approved extract must not be described."""
    dids, rids = _known_ids()
    found = find_identifiers(question)
    unknown = [i for i in found if i not in dids and i not in rids]
    known_dids = [i for i in found if i in dids]
    facts = []
    for did in known_dids:  # rule-engine facts for known DIDs (bytes never come from the LLM)
        facts += did_fact_table(did)
    return {"identifiers": [f"0x{i:04X}" for i in found], "unknown": [f"0x{i:04X}" for i in unknown], "rule_facts": facts}


def did_fact_table(did_id: int) -> list[dict]:
    """Authoritative read/write outcomes for one DID in each session / security state."""
    did = next(d for d in ecu_extract()["dids"] if int(d["did"], 16) == did_id)
    hi_lo = f"{did_id >> 8:02X} {did_id & 0xFF:02X}"
    data = encode_value(did).hex(" ").upper()
    states = [("default", []), ("extended", []), ("extended", [1])]
    rows = [("read", f"22 {hi_lo}", s, u) for s, u in states]
    rows += [("write", f"2E {hi_lo} {data}", s, u) for s, u in states]
    out = []
    for op, req, sess, unl in rows:
        r = validate_request(req, sess, unl)
        out.append({"scenario": f"{op} in {sess} session, security {'level 1 unlocked' if unl else 'locked'}",
                    "request": r["request"], "expected": r["expected"],
                    "nrc": f"{r['nrc']} {r['nrc_name']}" if r["nrc"] else None, "citations": r["citations"]})
    return out


def pick_scenario(question: str, facts: list[dict]) -> dict | None:
    """Selects the fact-table row the question asks about (operation, session, security state)."""
    q = question.lower()
    op = "write" if re.search(r"\bwrit|\b2e\b", q) else ("read" if re.search(r"\bread|\b22\b", q) else None)
    sess = "default" if "default" in q else ("extended" if "extended" in q else None)
    if re.search(r"without (unlocking|security|unlock)|\blocked\b|not unlocked|no security", q):
        sec = "locked"
    elif re.search(r"unlocked|after unlocking|with security", q):
        sec = "level 1 unlocked"
    else:
        sec = None
    if not op or not sess:
        return None
    rows = [f for f in facts if f["scenario"].startswith(f"{op} in {sess}") and (sec is None or f["scenario"].endswith(f"security {sec}"))]
    if len({r["expected"] for r in rows}) == 1:  # unambiguous outcome
        return rows[0]
    return None


def rule_consistency(answer_text: str, question: str, facts: list[dict]) -> dict | None:
    """Rules decide: if the LLM states an NRC / byte value that contradicts the rule engine for the
    scenario the question describes, the answer is corrected."""
    row = pick_scenario(question, facts)
    if not row:
        return None
    rule_sentence = (f"Rule engine: {row['scenario']} -> request {row['request']} gives {row['expected']}"
                     + (f" (NRC {row['nrc']})." if row["nrc"] else " (positive response)."))
    allowed = set(row["expected"].split()) | set(row["request"].split())
    stated = {c.upper() for c in re.findall(r"0x([0-9A-Fa-f]{2})\b", answer_text)}
    conflict = sorted(stated - allowed)
    return {"row": row, "sentence": rule_sentence, "conflict": conflict}


def _unknown_answer(unknown: list[str]) -> dict:
    lines = []
    for ident in unknown:
        v = int(ident, 16)
        neg = validate_request(f"22 {v >> 8:02X} {v & 0xFF:02X}")
        lines.append(f"{ident} is not defined in the approved BCM-X1 extract, so no data length, session or "
                     f"meaning can be given. The only valid test is a negative one: {neg['request']} -> "
                     f"{neg['expected']} ({neg['nrc_name']}).")
    return {"answer": " ".join(lines), "citations": ["ECU-DID-LIST", "ECU-RID-LIST", "STD-SVC-22", "STD-NRC-31"],
            "confidence": "high", "insufficient_evidence": True, "source": "identifier_guard"}


def answer(question: str, collections: list[str] | None = None) -> dict:
    t0 = time.perf_counter()
    cfg = load_config()
    ret = Retriever.instance().search(question, collections)
    hits = ret["hits"]
    retrieved_ids = [h["chunk_id"] for h in hits]
    guard = identifier_guard(question)
    system, prompt_version = prompt("qa")
    base = {"question": question, "retrieved": hits, "retrieval_latency_ms": ret["latency_ms"],
            "guard": guard, "model": cfg["llm"]["model"], "prompt_version": prompt_version}

    if guard["unknown"] and not guard["rule_facts"]:
        out = _unknown_answer(guard["unknown"])
        return {**base, **out, "citations_valid": True, "rejected_citations": [],
                "latency_s": round(time.perf_counter() - t0, 2)}

    if ret["max_dense"] < cfg["retrieval"]["min_dense_score"]:
        return {**base, "answer": "This question is outside the approved diagnostic knowledge base, so it cannot be "
                                  "answered with cited evidence.", "citations": [], "confidence": "low",
                "insufficient_evidence": True, "source": "out_of_scope_guard", "citations_valid": True,
                "rejected_citations": [], "latency_s": round(time.perf_counter() - t0, 2)}

    evidence = "\n\n".join(f"[{h['chunk_id']}] ({h['collection']}, {h['source']} v{h['version']})\n{h['text']}" for h in hits)
    user = f"Question: {question}\n\nEvidence chunks:\n{evidence}"
    if guard["rule_facts"]:
        user += ("\n\nRule-engine facts (authoritative, computed from the ECU extract - use these for any byte "
                 "value or NRC; cite the evidence chunks above):\n" + "\n".join(
                     f"- {f['scenario']}: {f['request']} -> {f['expected']}" + (f" ({f['nrc']})" if f["nrc"] else "")
                     for f in guard["rule_facts"]))
    try:
        data, meta = llm.chat_json(system, user)
        source = "llm"
    except llm.LLMUnavailable:
        data, meta = {}, {"json_valid": False, "latency_s": 0, "raw": ""}
        source = "extractive_fallback"

    cited = [c for c in data.get("citations", []) if isinstance(c, str)]
    valid = [c for c in cited if c in retrieved_ids]
    rejected = [c for c in cited if c not in retrieved_ids]
    text = data.get("answer", "")
    if source == "llm" and (not valid or not text):
        source = "guardrail_fallback"  # uncited or invalid output is never shown as an answer
    if source != "llm":
        text = "Most relevant evidence (LLM answer unavailable or uncited): " + " | ".join(
            f"[{h['chunk_id']}] {h['text'][:220]}" for h in hits[:2])
        valid, data = retrieved_ids[:2], {"confidence": "low", "insufficient_evidence": False}
    check = rule_consistency(text, question, guard["rule_facts"]) if guard["rule_facts"] else None
    rule_conflict = []
    if check:
        if check["conflict"]:
            rule_conflict = check["conflict"]
            text = (f"{check['sentence']} The LLM draft stated {', '.join('0x' + c for c in rule_conflict)}, which contradicts "
                    f"the ECU extract, so it was corrected by the rule engine.")
            source = "rule_engine_correction"
        else:
            text = f"{text} {check['sentence']}"
        rule_cites = {c for c in check["row"]["citations"] if c in Retriever.instance().by_id}
        valid = sorted(rule_cites if rule_conflict else set(valid) | rule_cites)
    return {**base, "answer": text, "citations": valid, "rejected_citations": rejected, "rule_conflict": rule_conflict,
            "citations_valid": not rejected, "confidence": data.get("confidence", "low"),
            "insufficient_evidence": bool(data.get("insufficient_evidence", False)), "source": source,
            "json_valid": meta.get("json_valid"), "llm_latency_s": meta.get("latency_s"),
            "latency_s": round(time.perf_counter() - t0, 2)}
