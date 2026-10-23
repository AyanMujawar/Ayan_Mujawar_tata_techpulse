"""Test-case generation: the LLM proposes scenario categories (with cited rationale), guardrails
filter them, the deterministic checklist fills coverage gaps, and the rule engine builds and
simulates every request/expected response on the virtual BCM-X1. Output status is DRAFT."""
from __future__ import annotations

import time

from . import llm
from .config import ecu_extract, load_config, prompt, requirements
from .retrieval import Retriever
from .rules import FIXED_SEEDS, VirtualECU, parse_hex

CATEGORIES = load_config()["generation"]["categories"]


def _h(v):
    return int(v, 16)


def _hex(n: int, width: int = 2) -> str:
    return " ".join(f"{b:02X}" for b in n.to_bytes(width, "big"))


def _unknown(table: dict, start: int) -> int:
    probe = start + 1
    while probe in table:
        probe += 1
    return probe & 0xFFFF


def _facts(req: dict) -> dict:
    ex = ecu_extract()
    sid = _h(req["service"])
    f = {"sid": sid, "sub": _h(req["subfunction"]) if req.get("subfunction") else None,
         "ident": _h(req["identifier"]) if req.get("identifier") else None}
    dids = {_h(d["did"]): d for d in ex["dids"]}
    rids = {_h(r["rid"]): r for r in ex["rids"]}
    f["did"] = dids.get(f["ident"]) if sid in (0x22, 0x2E) else None
    f["rid"] = rids.get(f["ident"]) if sid == 0x31 else None
    f["dids"], f["rids"] = dids, rids
    return f


def scenario_plan(req: dict, category: str) -> dict | None:
    """Deterministic test design. Returns None when a category does not apply to a requirement.
    session/level = precondition state; requests = the test requests sent after preconditions."""
    f = _facts(req)
    sid, sub, ident = f["sid"], f["sub"], f["ident"]
    P = lambda session="default", level=None, requests=(), pre=(): {"session": session, "level": level,
                                                                     "pre": list(pre), "requests": list(requests)}
    if sid == 0x22:
        d = f["did"]
        did = _hex(ident)
        sess = "default" if "default" in d["read_sessions"] else d["read_sessions"][-1]
        lvl = d.get("read_security")
        return {
            "positive": P(sess, lvl, [f"22 {did}"]),
            "wrong_session": P("default", None, [f"22 {did}"]) if sess != "default" else None,
            "security_locked": P(sess, None, [f"22 {did}"]) if lvl else None,
            "wrong_length": P(sess, lvl, [f"22 {did[:2]}"]),
            "unsupported_identifier": P(sess, lvl, [f"22 {_hex(_unknown(f['dids'], ident))}"]),
        }.get(category)
    if sid == 0x2E:
        d = f["did"]
        did = _hex(ident)
        if d["access"] != "RW":
            return {"read_only_rejected": P("extended", 1, [f"2E {did} " + " ".join(["00"] * d["length"])]),
                    "wrong_session": P("default", None, [f"2E {did} " + " ".join(["00"] * d["length"])])}.get(category)
        sess, lvl = d["write_sessions"][0], d.get("write_security")
        value = req.get("value") or _default_hex(d)
        data = " ".join(value[i:i + 2] for i in range(0, len(value), 2))
        plans = {
            "positive": P(sess, lvl, [f"2E {did} {data}", f"22 {did}"] if sess in d["read_sessions"] else [f"2E {did} {data}"]),
            "wrong_session": P("default", None, [f"2E {did} {data}"]),
            "security_locked": P(sess, None, [f"2E {did} {data}"]) if lvl else None,
            "wrong_length": P(sess, lvl, [f"2E {did} {' '.join(data.split()[:-1])}".strip()]),
            "out_of_range": P(sess, lvl, [f"2E {did} {_hex(d['max'] + 1, d['length'])}"]) if "max" in d else None,
            "unsupported_identifier": P(sess, lvl, [f"2E {_hex(_unknown(f['dids'], ident))} {data}"]),
        }
        return plans.get(category)
    if sid == 0x31:
        r = f["rid"]
        rid = _hex(ident)
        sess, lvl = r["sessions"][0], r.get("security")
        subs = r["subfunctions"]
        positive = [f"31 01 {rid}"] + ([f"31 03 {rid}"] if "0x03" in subs else []) + ([f"31 02 {rid}"] if "0x02" in subs else [])
        later = "03" if "0x03" in subs else ("02" if "0x02" in subs else None)
        return {
            "positive": P(sess, lvl, positive),
            "wrong_session": P("default", None, [f"31 01 {rid}"]),
            "security_locked": P(sess, None, [f"31 01 {rid}"]) if lvl else None,
            "wrong_length": P(sess, lvl, [f"31 01 {rid[:2]}"]),
            "sequence_error": P(sess, lvl, [f"31 {later} {rid}"]) if later else None,
            "unsupported_identifier": P(sess, lvl, [f"31 01 {_hex(_unknown(f['rids'], ident))}"]),
        }.get(category)
    if sid == 0x27:
        return {
            "positive": P("extended", 1, ["22 4A 61"]),  # unlock, then prove access to a level-1 protected DID
            "wrong_session": P("default", None, ["27 01"]),
            "wrong_length": P("extended", None, ["27 01", "27 02 00 00 00"]),
            "sequence_error": P("extended", None, ["27 02 00 00 00 00"]),
            "invalid_key": P("extended", None, ["27 01", "27 02 00 00 00 01", "27 01", "27 02 00 00 00 02",
                                                "27 01", "27 02 00 00 00 03", "27 01"]),
            "unsupported_identifier": P("extended", None, ["27 05"]),
        }.get(category)
    if sid == 0x10:
        target = {0x01: "01", 0x02: "02", 0x03: "03"}[sub]
        if target == "02":
            pos = P("default", None, ["10 03", "10 02", "22 F1 86"])
        elif target == "01":
            pos = P("extended", None, ["10 01", "22 F1 86"])
        else:
            pos = P("default", None, ["10 03", "22 F1 86"])
        return {
            "positive": pos,
            "wrong_length": P("default", None, [f"10 {target} 00"]),
            "sequence_error": P("default", None, ["10 02"]) if target == "02" else None,
            "unsupported_identifier": P("default", None, ["10 04"]),
        }.get(category)
    if sid == 0x11:
        return {"positive": P("extended", None, [f"11 {sub:02X}", "22 F1 86"]),
                "wrong_length": P("default", None, [f"11 {sub:02X} 00"]),
                "unsupported_identifier": P("default", None, ["11 02"])}.get(category)
    if sid == 0x3E:
        return {"positive": P("extended", None, ["3E 00", "3E 80"]),
                "wrong_length": P("extended", None, ["3E"]),
                "unsupported_identifier": P("extended", None, ["3E 01"])}.get(category)
    if sid == 0x19:
        request = f"19 {sub:02X} FF" if sub in (0x01, 0x02) else "19 0A"
        return {"positive": P("default", None, [request]),
                "wrong_length": P("default", None, [f"19 {sub:02X}"] if sub in (0x01, 0x02) else ["19 0A FF"]),
                "unsupported_identifier": P("default", None, ["19 03 FF"])}.get(category)
    if sid == 0x14:
        return {"positive": P("default", None, ["14 FF FF FF", "19 01 FF"]),
                "wrong_length": P("default", None, ["14 FF FF"]),
                "unsupported_identifier": P("default", None, ["14 12 34 56"])}.get(category)
    if sid == 0x85:
        return {"positive": P("extended", None, ["85 02", "85 01"]),
                "wrong_session": P("default", None, ["85 02"]),
                "wrong_length": P("extended", None, ["85 02 00"]),
                "unsupported_identifier": P("extended", None, ["85 03"])}.get(category)
    if sid == 0x28:
        return {"positive": P("extended", None, [f"28 {sub:02X} 01", "28 00 01"]),
                "wrong_session": P("default", None, [f"28 {sub:02X} 01"]),
                "wrong_length": P("extended", None, [f"28 {sub:02X}"]),
                "out_of_range": P("extended", None, [f"28 {sub:02X} 05"]),
                "unsupported_identifier": P("extended", None, ["28 02 01"])}.get(category)
    return None


def _default_hex(did: dict) -> str:
    from .rules import encode_value
    return encode_value(did).hex().upper()


def applicable_categories(req: dict) -> list[str]:
    """Deterministic coverage checklist (Demo OEM spec section 7)."""
    return [c for c in CATEGORIES if scenario_plan(req, c) is not None]


def materialise(req: dict, category: str, plan: dict) -> dict:
    """Runs preconditions + test requests through the virtual ECU and records expected patterns."""
    ecu = VirtualECU()
    levels = {lv["level"]: lv for lv in ecu_extract()["security_levels"]}
    steps, cites = [], []

    def run(request: str, purpose: str, **extra):
        res = ecu.process(parse_hex(request))
        steps.append({"request": request, "expected": res.expected, "purpose": purpose,
                      "nrc": f"0x{res.nrc:02X}" if res.nrc is not None else None, "nrc_name": res.nrc_name,
                      "explanation": res.explanation, **extra})
        cites.extend(res.citations)
        return res

    if plan["session"] == "extended":
        run("10 03", "precondition: enter extended session")
    elif plan["session"] == "programming":
        run("10 03", "precondition: enter extended session")
        run("10 02", "precondition: enter programming session")
    if plan["level"]:
        lvl = plan["level"]
        run(f"27 {lvl:02X}", f"precondition: request seed for security level {lvl}")
        key = VirtualECU.key_for(FIXED_SEEDS[lvl], levels[lvl])
        res = ecu.process(bytes([0x27, lvl + 1]) + key.to_bytes(4, "big"))
        steps.append({"request": f"27 {lvl + 1:02X} <key = seed XOR {levels[lvl]['xor_constant']}>", "expected": res.expected,
                      "purpose": f"precondition: send key for level {lvl}", "nrc": None, "nrc_name": None,
                      "explanation": res.explanation, "dynamic_key": True, "request_prefix": f"27 {lvl + 1:02X}",
                      "xor_constant": levels[lvl]["xor_constant"]})
        cites.extend(res.citations)
    for i, request in enumerate(plan["pre"] + plan["requests"]):
        run(request, "test step" if i >= len(plan["pre"]) else "precondition")
    final = steps[-1]
    pre = [f"Session before test: {plan['session']}",
           f"Security level {plan['level']} unlocked" if plan["level"] else "Security access locked",
           "Bench ECU, ignition on, vehicle stationary (Demo OEM spec section 8)"]
    return {
        "id": f"TC-{req['id'][4:]}-{category.upper()}",
        "requirement_id": req["id"], "category": category,
        "title": f"{req['title']} - {category.replace('_', ' ')}",
        "preconditions": pre, "steps": steps,
        "expected_final": final["expected"], "expected_nrc": final["nrc"],
        "pass_criteria": "Every response matches its expected pattern exactly ('XX' = any byte, '<none>' = no response within P2).",
        "rule_citations": sorted(set(cites)),
    }


def generate(requirement_id: str, use_llm: bool = True) -> dict:
    """Generates DRAFT test cases for one requirement. Returns cases plus guardrail metrics."""
    t0 = time.perf_counter()
    req = requirements()[requirement_id]
    applicable = applicable_categories(req)
    ret = Retriever.instance().search(f"{req['title']}. {req['text']}")
    retrieved = [h["chunk_id"] for h in ret["hits"]]
    system, prompt_version = prompt("scenario")
    llm_meta = {"used": False, "json_valid": None, "latency_s": 0.0}
    proposed, rejected, rationale = [], [], {}

    if use_llm:
        evidence = "\n\n".join(f"[{h['chunk_id']}] {h['text']}" for h in ret["hits"])
        user = (f"Requirement {req['id']}: {req['text']}\nService: {req['service']}  Identifier: {req.get('identifier')}  "
                f"Sub-function: {req.get('subfunction')}\n\nEvidence chunks:\n{evidence}")
        try:
            data, meta = llm.chat_json(system, user)
            llm_meta = {"used": True, "json_valid": meta["json_valid"], "latency_s": meta["latency_s"]}
            for s in data.get("scenarios", []) if isinstance(data.get("scenarios"), list) else []:
                cat = str(s.get("category", "")).strip().lower()
                cits = [c for c in s.get("citations", []) if c in retrieved]
                if cat in applicable and cat not in proposed:
                    proposed.append(cat)
                    rationale[cat] = {"rationale": s.get("rationale", ""), "citations": cits,
                                      "invalid_citations": [c for c in s.get("citations", []) if c not in retrieved]}
                elif cat not in proposed:
                    rejected.append({"category": cat, "reason": "not applicable per ECU extract / rule engine" if cat in CATEGORIES + ["invalid_key"] else "unknown category"})
        except llm.LLMUnavailable:
            llm_meta = {"used": False, "json_valid": None, "latency_s": 0.0, "error": "Ollama not reachable"}

    missing = [c for c in applicable if c not in proposed]
    cases = []
    for cat in proposed + missing:
        case = materialise(req, cat, scenario_plan(req, cat))
        src = "llm" if cat in proposed else "rule_completion"
        info = rationale.get(cat, {"rationale": "Added by the deterministic coverage checklist (Demo OEM spec section 7).",
                                   "citations": ["OEM-SPEC-7"], "invalid_citations": []})
        case.update({"source": src, "rationale": info["rationale"],
                     "citations": sorted(set(info["citations"] + case["rule_citations"] + [req["id"]])),
                     "invalid_llm_citations": info["invalid_citations"], "status": "DRAFT",
                     "model": load_config()["llm"]["model"] if src == "llm" else "rule-engine",
                     "prompt_version": prompt_version, "retrieved": retrieved})
        cases.append(case)
    return {"requirement": req, "applicable": applicable, "llm_proposed": proposed, "llm_rejected": rejected,
            "rule_added": missing, "llm": llm_meta, "cases": cases,
            "llm_coverage": round(len(proposed) / len(applicable), 3) if applicable else 1.0,
            "latency_s": round(time.perf_counter() - t0, 2)}
