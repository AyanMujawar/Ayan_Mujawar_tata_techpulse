"""Knowledge-base ingestion: loads Input_Data, normalises hex notation and builds
structure-aware chunks with stable IDs used for citations.

Chunk ID scheme
  STD-GEN-*, STD-SVC-<SID>, STD-NRC-<code>   standard_uds  (service guide, NRC catalogue)
  OEM-SPEC-<section>                          oem_spec      (Demo OEM spec)
  ECU-SVC-<SID>, ECU-DID-<DID>, ECU-RID-<RID>,
  ECU-SEC-L<n>, ECU-SESSIONS, ECU-*-LIST      ecu_extract   (BCM-X1 extract, one row = one chunk)
  REQ-BCM-<n>, TC-EX-<n>                      project_reqs  (requirements, reviewed tests)
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from .config import load_config, path


@dataclass
class Chunk:
    chunk_id: str
    collection: str
    source: str
    title: str
    text: str
    version: str


def normalise_hex(text: str) -> str:
    """Unifies hex notation (22h, $22, 0x22) to 0x22 so identifiers match exactly."""
    text = re.sub(r"\$([0-9A-Fa-f]{2,6})\b", r"0x\1", text)
    text = re.sub(r"\b([0-9A-Fa-f]{2,4})h\b", r"0x\1", text)
    return text


def _split_words(text: str, max_tokens: int, overlap: int) -> list[str]:
    """~token windows (1 word ~ 1.3 tokens) with overlap, for long prose sections."""
    words = text.split()
    size, step = int(max_tokens / 1.3), int((max_tokens - overlap) / 1.3)
    if len(words) <= size:
        return [text]
    return [" ".join(words[i:i + size]) for i in range(0, len(words), step) if words[i:i + size]]


def _markdown_sections(md: str) -> tuple[str, list[tuple[str, str]]]:
    version = (re.search(r"Version ([\d.]+)", md) or [None, "1.0"])[1]
    parts = re.split(r"^## +(.+)$", md, flags=re.M)
    return version, [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts), 2)]


def _std_id(heading: str) -> str:
    m = re.match(r"Services? 0x([0-9A-F]{2})", heading)
    if m:
        return f"STD-SVC-{m.group(1)}"
    return {"General message rules": "STD-GEN-MSG", "NRC evaluation order": "STD-GEN-NRCORDER"}.get(
        heading, "STD-GEN-" + re.sub(r"\W+", "", heading.upper())[:12])


def build_chunks() -> list[Chunk]:
    cfg = load_config()["retrieval"]
    base = path("input_data")
    chunks: list[Chunk] = []

    def add(cid, coll, src, title, text, ver):
        text = normalise_hex(text)
        windows = _split_words(text, cfg["chunk_tokens"], cfg["chunk_overlap"])
        for i, w in enumerate(windows):
            chunks.append(Chunk(cid if i == 0 else f"{cid}-p{i + 1}", coll, src, title, w, ver))

    # standard_uds -------------------------------------------------------------
    src = "standard_uds/uds_service_guide.md"
    ver, sections = _markdown_sections((base / src).read_text(encoding="utf-8"))
    for heading, body in sections:
        add(_std_id(heading), "standard_uds", src, heading, f"{heading}. {body}", ver)
    nrc = json.loads((base / "standard_uds/nrc_catalogue.json").read_text(encoding="utf-8"))
    for n in nrc["nrcs"]:
        add(f"STD-NRC-{n['code'][2:].upper()}", "standard_uds", "standard_uds/nrc_catalogue.json",
            f"NRC {n['code']} {n['name']}", f"Negative response code {n['code']} {n['name']}: {n['meaning']}", nrc["version"])

    # oem_spec -----------------------------------------------------------------
    src = "oem_spec/demo_oem_diagnostic_spec.md"
    ver, sections = _markdown_sections((base / src).read_text(encoding="utf-8"))
    for heading, body in sections:
        num = heading.split()[0]
        add(f"OEM-SPEC-{num}", "oem_spec", src, f"Demo OEM {heading}", f"Demo OEM specification section {heading}. {body}", ver)

    # ecu_extract (one table row = one chunk) -----------------------------------
    src = "ecu_extract/bcm_x1_ecu_extract.json"
    ex = json.loads((base / src).read_text(encoding="utf-8"))
    ver = ex["version"]
    add("ECU-SESSIONS", "ecu_extract", src, "BCM-X1 diagnostic sessions",
        "BCM-X1 sessions: " + "; ".join(f"{s['id']} {s['name']} - {s['description']}" for s in ex["sessions"]), ver)
    for lv in ex["security_levels"]:
        add(f"ECU-SEC-L{lv['level']}", "ecu_extract", src, f"BCM-X1 security level {lv['level']}",
            f"BCM-X1 SecurityAccess level {lv['level']}: seed request sub-function {lv['seed_subfunction']}, send key "
            f"sub-function {lv['key_subfunction']}, available in {', '.join(lv['sessions'])} session, seed and key "
            f"{lv['seed_length']} bytes, maximum {lv['max_attempts']} invalid keys then {lv['lockout_delay_s']} s lockout.", ver)
    for s in ex["services"]:
        extra = f" Sub-functions: {', '.join(s['subfunctions'])}." if s.get("subfunctions") else ""
        extra += f" Requires security level {s['security_level']}." if s.get("security_level") else ""
        extra += f" Supported DTC groups: {', '.join(s['dtc_groups'])}." if s.get("dtc_groups") else ""
        add(f"ECU-SVC-{s['sid'][2:]}", "ecu_extract", src, f"BCM-X1 service {s['sid']} {s['name']}",
            f"BCM-X1 supports service {s['sid']} {s['name']} in sessions: {', '.join(s['sessions'])}.{extra}", ver)
    add("ECU-SVC-LIST", "ecu_extract", src, "BCM-X1 supported services",
        "Services implemented by BCM-X1: " + ", ".join(f"{s['sid']} {s['name']}" for s in ex["services"])
        + ". Any other service identifier is answered with NRC 0x11 serviceNotSupported.", ver)
    for d in ex["dids"]:
        label = re.sub(r"(?<!^)(?=[A-Z])", " ", d["name"]).lower() + (f" ({d['alias']})" if d.get("alias") else "")
        parts = [f"BCM-X1 DID {d['did']} {d['name']} - {label}: length {d['length']} byte(s), access {'read/write' if d['access'] == 'RW' else 'read-only'},",
                 f"encoding {d['encoding']}, readable in {', '.join(d['read_sessions'])} session(s)."]
        if d.get("read_security"):
            parts.append(f"Reading requires security level {d['read_security']}.")
        if d["access"] == "RW":
            sec = f"security level {d['write_security']}" if d.get("write_security") else "no security access"
            parts.append(f"Writable in {', '.join(d['write_sessions'])} session with {sec}.")
        if "min" in d:
            parts.append(f"Allowed range {d['min']} to {d['max']}{' ' + d['unit'] if d.get('unit') else ''}.")
        for k in ("unit", "scaling", "note"):
            if d.get(k) and not (k == "unit" and "min" in d):
                parts.append(f"{k.capitalize()}: {d[k]}.")
        if d.get("values"):
            parts.append("Values: " + ", ".join(f"{k}={v}" for k, v in d["values"].items()) + ".")
        parts.append(f"Bench default value: {d['default']}.")
        add(f"ECU-DID-{d['did'][2:]}", "ecu_extract", src, f"DID {d['did']} {d['name']}", " ".join(parts), ver)
    add("ECU-DID-LIST", "ecu_extract", src, "BCM-X1 DID list",
        "Complete list of data identifiers in the approved BCM-X1 extract: " + ", ".join(f"{d['did']} {d['name']}" for d in ex["dids"])
        + ". Any DID not in this list is unsupported and answered with NRC 0x31 requestOutOfRange.", ver)
    for r in ex["rids"]:
        sec = f"security level {r['security']}" if r.get("security") else "no security access"
        add(f"ECU-RID-{r['rid'][2:]}", "ecu_extract", src, f"RID {r['rid']} {r['name']}",
            f"BCM-X1 routine {r['rid']} {r['name']}: runs in {', '.join(r['sessions'])} session with {sec}; "
            f"supported routine control types {', '.join(r['subfunctions'])}; result record {r['result']}. {r.get('note', '')}", ver)
    add("ECU-RID-LIST", "ecu_extract", src, "BCM-X1 RID list",
        "Routine identifiers in the approved BCM-X1 extract: " + ", ".join(f"{r['rid']} {r['name']}" for r in ex["rids"])
        + ". Any other RID is answered with NRC 0x31.", ver)
    add("ECU-DTC-LIST", "ecu_extract", src, "BCM-X1 bench DTCs",
        "DTCs stored on the BCM-X1 bench ECU: " + "; ".join(f"{d['dtc']} {d['name']} status {d['status']}" for d in ex["dtcs"])
        + ". DTCStatusAvailabilityMask is 0xFF and the DTC format identifier is 0x01.", ver)

    # project_reqs -------------------------------------------------------------
    reqs = json.loads((base / "project_reqs/diagnostic_requirements.json").read_text(encoding="utf-8"))
    for r in reqs["requirements"]:
        add(r["id"], "project_reqs", "project_reqs/diagnostic_requirements.json", f"{r['id']} {r['title']}",
            f"Requirement {r['id']} ({r['title']}): {r['text']}", reqs["version"])
    tests = json.loads((base / "project_reqs/existing_test_cases.json").read_text(encoding="utf-8"))
    for t in tests["tests"]:
        steps = "; ".join(f"send {a} expect {b}" for a, b in t["steps"])
        add(t["id"], "project_reqs", "project_reqs/existing_test_cases.json", f"{t['id']} for {t['requirement']}",
            f"Reviewed test {t['id']} for {t['requirement']}, category {t['category']}: {steps}. Pass: {t['pass']}", "1.0")
    return chunks


def chunk_dicts() -> list[dict]:
    return [asdict(c) for c in build_chunks()]
