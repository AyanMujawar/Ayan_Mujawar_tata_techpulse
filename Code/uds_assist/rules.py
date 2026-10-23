"""Deterministic UDS rule engine and virtual BCM-X1.

The LLM never produces bytes. Every request is checked and every expected response is
built here, from the ECU extract (Input_Data/ecu_extract) and the Demo OEM rules.
Each decision records the knowledge-base chunk IDs it relied on, so rule outputs are
cited exactly like LLM answers.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from .config import ecu_extract, nrc_names

SESSION_IDS = {"default": 0x01, "programming": 0x02, "extended": 0x03}
SESSION_NAMES = {v: k for k, v in SESSION_IDS.items()}
SUPPRESS_CAPABLE = {0x10, 0x11, 0x28, 0x3E, 0x85}
P2_BYTES = [0x00, 0x32]       # 50 ms (OEM-SPEC-2)
P2STAR_BYTES = [0x01, 0xF4]   # 5000 ms in 10 ms steps (OEM-SPEC-2)
DTC_AVAILABILITY_MASK = 0xFF
FIXED_SEEDS = {1: 0x11223344, 3: 0x55667788}  # deterministic bench seeds for the simulator


def parse_hex(text: str) -> bytes:
    """Accepts '22 F1 90', '22F190', '0x22 0xF1 0x90' or '22h F1h 90h'."""
    cleaned = re.sub(r"0x|h\b|\$|,", " ", text.strip(), flags=re.I)
    cleaned = re.sub(r"\s+", "", cleaned)
    if not cleaned or len(cleaned) % 2 or not re.fullmatch(r"[0-9A-Fa-f]+", cleaned):
        raise ValueError(f"not a valid hex byte string: {text!r}")
    return bytes.fromhex(cleaned)


def to_hex(data: bytes | list[int] | None) -> str | None:
    if data is None:
        return None
    return " ".join(f"{b:02X}" for b in data)


def _h(value: str) -> int:
    return int(value, 16)


def encode_value(did: dict, value=None) -> bytes:
    """Encodes a DID value (default if None) into its raw data record."""
    value = did.get("default") if value is None else value
    enc, length = did["encoding"], did["length"]
    if enc == "ascii":
        return str(value).encode("ascii").ljust(length, b" ")[:length]
    if enc in ("bcd", "raw"):
        return bytes.fromhex(str(value))
    return int(value).to_bytes(length, "big")


def is_static(did: dict) -> bool:
    """Static identification data can be checked byte-exact; live values are wildcarded."""
    return did["encoding"] in ("ascii", "bcd") or did["did"] in ("0x4A40",)


@dataclass
class Result:
    request: str
    response: str | None          # what the virtual ECU answered (None = no response)
    expected: str                 # expected pattern for a test ('XX' = any byte, '<none>' = silence)
    positive: bool
    nrc: int | None = None
    nrc_name: str | None = None
    explanation: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    state_after: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["nrc"] = f"0x{self.nrc:02X}" if self.nrc is not None else None
        return d


class VirtualECU:
    """Stateful model of BCM-X1 that answers raw UDS requests per the extract and OEM spec."""

    def __init__(self, session: str = "default", unlocked: set[int] | None = None):
        ex = ecu_extract()
        self.services = {_h(s["sid"]): s for s in ex["services"]}
        self.dids = {_h(d["did"]): d for d in ex["dids"]}
        self.rids = {_h(r["rid"]): r for r in ex["rids"]}
        self.levels = {lv["level"]: lv for lv in ex["security_levels"]}
        self.dtcs = ex["dtcs"]
        self.dtc_groups = {_h(g) for g in self.services[0x14]["dtc_groups"]}
        self.session = session
        self.unlocked: set[int] = set(unlocked or ())
        self.pending_seed: dict[int, int] = {}
        self.failed: dict[int, int] = {}
        self.locked_out: set[int] = set()
        self.started_routines: set[int] = set()
        self.download_active = False
        self.block_counter = 0
        self.values = {k: encode_value(v) for k, v in self.dids.items()}

    # ------------------------------------------------------------------ helpers
    def clone(self) -> "VirtualECU":
        return copy.deepcopy(self)

    def state(self) -> dict:
        return {"session": self.session, "unlocked_levels": sorted(self.unlocked),
                "pending_seed_levels": sorted(self.pending_seed), "locked_out": sorted(self.locked_out)}

    def _reset_security(self):
        self.unlocked.clear()
        self.pending_seed.clear()
        self.started_routines.clear()
        self.download_active = False

    @staticmethod
    def key_for(seed: int, level: dict) -> int:
        return seed ^ _h(level["xor_constant"])

    def _neg(self, req: bytes, sid: int, nrc: int, why: str, cites: list[str]) -> Result:
        name = nrc_names().get(nrc, "unknown")
        resp = [0x7F, sid, nrc]
        return Result(to_hex(req), to_hex(resp), to_hex(resp), False, nrc, name,
                      [why, f"Negative response 7F {sid:02X} {nrc:02X} ({name})."],
                      cites + ["STD-GEN-MSG", f"STD-NRC-{nrc:02X}"], self.state())

    def _pos(self, req: bytes, resp: list[int], pattern: list[str] | None, why: list[str],
             cites: list[str], suppress: bool = False) -> Result:
        if suppress:
            return Result(to_hex(req), None, "<none>", True, None, None,
                          why + ["suppressPosRspMsgIndicationBit set: no positive response is sent."],
                          cites + ["STD-GEN-MSG"], self.state())
        pat = " ".join(pattern) if pattern else to_hex(resp)
        return Result(to_hex(req), to_hex(resp), pat, True, None, None,
                      why + [f"Positive response SID = 0x{req[0]:02X} + 0x40 = 0x{resp[0]:02X}."],
                      cites + ["STD-GEN-MSG"], self.state())

    # ------------------------------------------------------------------ main entry
    def process(self, req: bytes) -> Result:
        if not req:
            raise ValueError("empty request")
        sid = req[0]
        std_sid = 0x34 if sid in (0x36, 0x37) else sid  # download services share one guide section
        svc_cite = [f"STD-SVC-{std_sid:02X}", f"ECU-SVC-{sid:02X}"]
        svc = self.services.get(sid)
        if svc is None:
            return self._neg(req, sid, 0x11, f"Service 0x{sid:02X} is not implemented in BCM-X1.",
                             ["STD-GEN-NRCORDER", "ECU-SVC-LIST"])
        if self.session not in svc["sessions"]:
            return self._neg(req, sid, 0x7F,
                             f"{svc['name']} is only allowed in {', '.join(svc['sessions'])} session(s); "
                             f"active session is {self.session}.", svc_cite + ["STD-GEN-NRCORDER"])
        handler = getattr(self, f"_svc_{sid:02X}", None)
        return handler(req, svc, svc_cite)

    # ------------------------------------------------------------------ services
    def _subfunction(self, req, svc, cites, exact_len=None):
        """Common sub-function checks: min length, supported sub-function, exact length."""
        sid = req[0]
        if len(req) < 2:
            return None, None, self._neg(req, sid, 0x13, "Request is shorter than SID + sub-function.", cites)
        sub, suppress = req[1] & 0x7F, bool(req[1] & 0x80) and sid in SUPPRESS_CAPABLE
        if f"0x{sub:02X}" not in svc.get("subfunctions", []):
            return None, None, self._neg(req, sid, 0x12, f"Sub-function 0x{sub:02X} is not supported by {svc['name']}.", cites)
        if exact_len is not None and len(req) != exact_len:
            return None, None, self._neg(req, sid, 0x13, f"{svc['name']} 0x{sub:02X} must be exactly {exact_len} bytes, got {len(req)}.", cites)
        return sub, suppress, None

    def _svc_10(self, req, svc, cites):
        sub, suppress, err = self._subfunction(req, svc, cites, exact_len=2)
        if err:
            return err
        target = SESSION_NAMES[sub]
        if target == "programming" and self.session == "default":
            return self._neg(req, 0x10, 0x22, "Demo OEM forbids default -> programming; enter the extended session first.",
                             cites + ["OEM-SPEC-3"])
        old = self.session
        self.session = target
        self._reset_security()
        return self._pos(req, [0x50, sub] + P2_BYTES + P2STAR_BYTES, None,
                         [f"Session change {old} -> {target}; security re-locked.",
                          "P2 = 50 ms (00 32), P2* = 5000 ms (01 F4)."], cites + ["OEM-SPEC-2", "OEM-SPEC-3"], suppress)

    def _svc_11(self, req, svc, cites):
        sub, suppress, err = self._subfunction(req, svc, cites, exact_len=2)
        if err:
            return err
        self.session = "default"
        self._reset_security()
        return self._pos(req, [0x51, sub], None, ["ECU resets and returns to the default session, security locked."],
                         cites, suppress)

    def _svc_14(self, req, svc, cites):
        if len(req) != 4:
            return self._neg(req, 0x14, 0x13, "ClearDiagnosticInformation needs SID + 3-byte DTC group (4 bytes).", cites)
        group = int.from_bytes(req[1:4], "big")
        if group not in self.dtc_groups:
            return self._neg(req, 0x14, 0x31, f"DTC group 0x{group:06X} is not supported.", cites)
        return self._pos(req, [0x54], None, [f"DTC group 0x{group:06X} cleared."], cites)

    def _svc_19(self, req, svc, cites):
        sub, _, err = self._subfunction(req, svc, cites)
        if err:
            return err
        need = 2 if sub == 0x0A else 3
        if len(req) != need:
            return self._neg(req, 0x19, 0x13, f"ReadDTCInformation 0x{sub:02X} must be {need} bytes.", cites)
        dtcs = [(int(d["dtc"], 16), int(d["status"], 16)) for d in self.dtcs]
        if sub == 0x0A:
            body = [b for code, st in dtcs for b in list(code.to_bytes(3, "big")) + [st]]
            return self._pos(req, [0x59, 0x0A, DTC_AVAILABILITY_MASK] + body, None, ["All supported DTCs reported."], cites + ["ECU-DTC-LIST"])
        mask = req[2]
        hits = [(c, s) for c, s in dtcs if s & mask]
        if sub == 0x01:
            return self._pos(req, [0x59, 0x01, DTC_AVAILABILITY_MASK, 0x01, 0x00, len(hits)], None,
                             [f"{len(hits)} DTC(s) match status mask 0x{mask:02X}; format 0x01."], cites + ["ECU-DTC-LIST"])
        body = [b for code, st in hits for b in list(code.to_bytes(3, "big")) + [st]]
        return self._pos(req, [0x59, 0x02, DTC_AVAILABILITY_MASK] + body, None,
                         [f"{len(hits)} DTC(s) match status mask 0x{mask:02X}."], cites + ["ECU-DTC-LIST"])

    def _svc_22(self, req, svc, cites):
        if len(req) < 3 or (len(req) - 1) % 2:
            return self._neg(req, 0x22, 0x13, "ReadDataByIdentifier needs SID + one or more 2-byte DIDs.", cites)
        resp, pattern, why, all_cites = [0x62], ["62"], [], list(cites)
        for i in range(1, len(req), 2):
            did_id = int.from_bytes(req[i:i + 2], "big")
            did = self.dids.get(did_id)
            c = [f"ECU-DID-{did_id:04X}"]
            if did is None:
                return self._neg(req, 0x22, 0x31, f"DID 0x{did_id:04X} is not in the BCM-X1 extract.", cites + ["ECU-DID-LIST"])
            if self.session not in did["read_sessions"]:
                return self._neg(req, 0x22, 0x31, f"DID 0x{did_id:04X} is only readable in {', '.join(did['read_sessions'])}.",
                                 cites + c + ["OEM-SPEC-5"])
            lvl = did.get("read_security")
            if lvl and lvl not in self.unlocked:
                return self._neg(req, 0x22, 0x33, f"DID 0x{did_id:04X} needs security level {lvl} unlocked.", cites + c + [f"ECU-SEC-L{lvl}"])
            data = list(self.values[did_id]) if did_id != 0xF186 else [SESSION_IDS[self.session]]
            resp += list(req[i:i + 2]) + data
            pattern += [f"{b:02X}" for b in req[i:i + 2]]
            pattern += [f"{b:02X}" for b in data] if (is_static(did) or did_id == 0xF186) else ["XX"] * did["length"]
            why.append(f"DID 0x{did_id:04X} {did['name']}: {did['length']} data byte(s).")
            all_cites += c
        return self._pos(req, resp, pattern, why, all_cites)

    def _svc_27(self, req, svc, cites):
        sub, _, err = self._subfunction(req, svc, cites)
        if err:
            return err
        level_no = sub if sub % 2 else sub - 1
        level = self.levels[level_no]
        c = cites + [f"ECU-SEC-L{level_no}", "OEM-SPEC-4"]
        if self.session not in level["sessions"]:
            return self._neg(req, 0x27, 0x7E, f"Security level {level_no} is only available in {', '.join(level['sessions'])} session.", c)
        if sub % 2:  # seed request
            if len(req) != 2:
                return self._neg(req, 0x27, 0x13, "Seed request must be exactly 2 bytes.", c)
            if level_no in self.locked_out:
                return self._neg(req, 0x27, 0x37, f"Level {level_no} locked out after too many invalid keys; delay not expired.", c)
            if level_no in self.unlocked:
                return self._pos(req, [0x67, sub, 0, 0, 0, 0], None, ["Level already unlocked: seed is all zeros."], c)
            seed = FIXED_SEEDS[level_no]
            self.pending_seed[level_no] = seed
            return self._pos(req, [0x67, sub] + list(seed.to_bytes(4, "big")), ["67", f"{sub:02X}", "XX", "XX", "XX", "XX"],
                             [f"Seed issued for level {level_no} (random on a real ECU, so the test accepts any 4 bytes)."], c)
        if len(req) != 2 + level["key_length"]:
            return self._neg(req, 0x27, 0x13, f"Send-key must be 2 + {level['key_length']} bytes.", c)
        if level_no not in self.pending_seed:
            return self._neg(req, 0x27, 0x24, "Key sent without a preceding seed request (requestSequenceError).", c)
        seed = self.pending_seed.pop(level_no)
        if int.from_bytes(req[2:], "big") != self.key_for(seed, level):
            self.failed[level_no] = self.failed.get(level_no, 0) + 1
            if self.failed[level_no] >= level["max_attempts"]:
                self.locked_out.add(level_no)
                return self._neg(req, 0x27, 0x36, f"Invalid key #{self.failed[level_no]}: maximum attempts reached, level blocked.", c)
            return self._neg(req, 0x27, 0x35, f"Invalid key (attempt {self.failed[level_no]} of {level['max_attempts']}).", c)
        self.failed[level_no] = 0
        self.unlocked.add(level_no)
        return self._pos(req, [0x67, sub], None, [f"Valid key: security level {level_no} unlocked."], c)

    def _svc_28(self, req, svc, cites):
        sub, suppress, err = self._subfunction(req, svc, cites, exact_len=3)
        if err:
            return err
        if req[2] not in (0x01, 0x02, 0x03):
            return self._neg(req, 0x28, 0x31, f"communicationType 0x{req[2]:02X} is not supported.", cites)
        return self._pos(req, [0x68, sub], None, [f"Communication control type 0x{sub:02X} applied."], cites, suppress)

    def _svc_2E(self, req, svc, cites):
        if len(req) < 4:
            return self._neg(req, 0x2E, 0x13, "WriteDataByIdentifier needs SID + DID + at least one data byte.", cites)
        did_id = int.from_bytes(req[1:3], "big")
        did = self.dids.get(did_id)
        c = cites + [f"ECU-DID-{did_id:04X}"]
        if did is None:
            return self._neg(req, 0x2E, 0x31, f"DID 0x{did_id:04X} is not in the BCM-X1 extract.", cites + ["ECU-DID-LIST"])
        if did["access"] != "RW":
            return self._neg(req, 0x2E, 0x31, f"DID 0x{did_id:04X} {did['name']} is read-only.", c + ["OEM-SPEC-5"])
        if len(req) != 3 + did["length"]:
            return self._neg(req, 0x2E, 0x13, f"DID 0x{did_id:04X} needs exactly {did['length']} data byte(s), got {len(req) - 3}.", c)
        if self.session not in did["write_sessions"]:
            return self._neg(req, 0x2E, 0x31, f"DID 0x{did_id:04X} is only writable in {', '.join(did['write_sessions'])}.", c + ["OEM-SPEC-5"])
        lvl = did.get("write_security")
        if lvl and lvl not in self.unlocked:
            return self._neg(req, 0x2E, 0x33, f"Writing DID 0x{did_id:04X} needs security level {lvl} unlocked.", c + [f"ECU-SEC-L{lvl}"])
        if "min" in did:
            value = int.from_bytes(req[3:], "big")
            if not did["min"] <= value <= did["max"]:
                return self._neg(req, 0x2E, 0x31, f"Value {value} outside allowed range {did['min']}..{did['max']}.", c)
        self.values[did_id] = bytes(req[3:])
        return self._pos(req, [0x6E] + list(req[1:3]), None, [f"DID 0x{did_id:04X} {did['name']} written."], c)

    def _svc_31(self, req, svc, cites):
        if len(req) < 4:
            return self._neg(req, 0x31, 0x13, "RoutineControl needs SID + sub-function + 2-byte RID.", cites)
        sub, _, err = self._subfunction(req, svc, cites)
        if err:
            return err
        rid_id = int.from_bytes(req[2:4], "big")
        rid = self.rids.get(rid_id)
        c = cites + [f"ECU-RID-{rid_id:04X}"]
        if rid is None:
            return self._neg(req, 0x31, 0x31, f"RID 0x{rid_id:04X} is not in the BCM-X1 extract.", cites + ["ECU-RID-LIST"])
        if self.session not in rid["sessions"]:
            return self._neg(req, 0x31, 0x31, f"RID 0x{rid_id:04X} only runs in {', '.join(rid['sessions'])} session.", c + ["OEM-SPEC-5"])
        if f"0x{sub:02X}" not in rid["subfunctions"]:
            return self._neg(req, 0x31, 0x12, f"RID 0x{rid_id:04X} does not support routine control type 0x{sub:02X}.", c + ["OEM-SPEC-5"])
        if rid.get("security") and rid["security"] not in self.unlocked:
            return self._neg(req, 0x31, 0x33, f"RID 0x{rid_id:04X} needs security level {rid['security']}.", c + [f"ECU-SEC-L{rid['security']}"])
        if sub in (0x02, 0x03) and rid_id not in self.started_routines:
            return self._neg(req, 0x31, 0x24, f"Routine 0x{rid_id:04X} was not started (requestSequenceError).", c)
        rid_bytes = list(req[2:4])
        if sub == 0x01:
            self.started_routines.add(rid_id)
            return self._pos(req, [0x71, 0x01] + rid_bytes + [_h(rid["result"])], None, [f"Routine {rid['name']} started."], c)
        if sub == 0x02:
            self.started_routines.discard(rid_id)
            return self._pos(req, [0x71, 0x02] + rid_bytes, None, [f"Routine {rid['name']} stopped."], c)
        return self._pos(req, [0x71, 0x03] + rid_bytes + [_h(rid["result"])], None, [f"Routine {rid['name']} results reported."], c)

    def _download_security(self, req, svc, cites):
        lvl = svc.get("security_level")
        if lvl and lvl not in self.unlocked:
            return self._neg(req, req[0], 0x33, f"{svc['name']} needs security level {lvl}.", cites + [f"ECU-SEC-L{lvl}"])
        return None

    def _svc_34(self, req, svc, cites):
        if len(req) < 5:
            return self._neg(req, 0x34, 0x13, "RequestDownload needs format, address/length format and memory data.", cites)
        err = self._download_security(req, svc, cites)
        if err:
            return err
        self.download_active, self.block_counter = True, 0
        return self._pos(req, [0x74, 0x20, 0x0F, 0xFF], None, ["Download accepted; maxNumberOfBlockLength 0x0FFF."], cites)

    def _svc_36(self, req, svc, cites):
        if len(req) < 2:
            return self._neg(req, 0x36, 0x13, "TransferData needs a block sequence counter.", cites)
        err = self._download_security(req, svc, cites)
        if err:
            return err
        if not self.download_active:
            return self._neg(req, 0x36, 0x24, "TransferData without an accepted RequestDownload.", cites)
        self.block_counter = req[1]
        return self._pos(req, [0x76, req[1]], None, [f"Block {req[1]} accepted."], cites)

    def _svc_37(self, req, svc, cites):
        err = self._download_security(req, svc, cites)
        if err:
            return err
        if not self.download_active:
            return self._neg(req, 0x37, 0x24, "RequestTransferExit without an active download.", cites)
        self.download_active = False
        return self._pos(req, [0x77], None, ["Transfer finished."], cites)

    def _svc_3E(self, req, svc, cites):
        sub, suppress, err = self._subfunction(req, svc, cites, exact_len=2)
        if err:
            return err
        return self._pos(req, [0x7E, 0x00], None, ["TesterPresent keeps the session alive (S3 = 5 s)."], cites + ["OEM-SPEC-2"], suppress)

    def _svc_85(self, req, svc, cites):
        sub, suppress, err = self._subfunction(req, svc, cites, exact_len=2)
        if err:
            return err
        return self._pos(req, [0xC5, sub], None, [f"DTC setting {'on' if sub == 1 else 'off'}."], cites, suppress)


# ---------------------------------------------------------------------- public helpers
def validate_request(request_hex: str, session: str = "default", unlocked: list[int] | None = None,
                     seed_requested_level: int | None = None) -> dict:
    """Validates one request in a given ECU state and returns the expected response."""
    ecu = VirtualECU(session=session, unlocked=set(unlocked or []))
    if seed_requested_level:
        ecu.pending_seed[seed_requested_level] = FIXED_SEEDS[seed_requested_level]
    try:
        req = parse_hex(request_hex)
    except ValueError as exc:
        return {"valid_format": False, "error": str(exc)}
    result = ecu.process(req).as_dict()
    result["valid_format"] = True
    result["state_before"] = {"session": session, "unlocked_levels": sorted(unlocked or []),
                              "seed_requested_level": seed_requested_level}
    return result


def pattern_matches(pattern: str, actual: str | None) -> bool:
    """True when an actual response (hex) matches an expected pattern ('XX' wildcard, '<none>')."""
    if pattern == "<none>":
        return actual is None
    if actual is None:
        return False
    exp, act = pattern.split(), actual.upper().split()
    return len(exp) == len(act) and all(e == "XX" or e == a for e, a in zip(exp, act))


def check_pair(request_hex: str, response_hex: str | None, session: str = "default",
               unlocked: list[int] | None = None, seed_requested_level: int | None = None) -> dict:
    """Judges whether a proposed response is the correct ECU behaviour for a request."""
    expected = validate_request(request_hex, session, unlocked, seed_requested_level)
    if not expected.get("valid_format"):
        return {"valid": False, "reason": expected["error"], "expected": None}
    ok = pattern_matches(expected["expected"], response_hex.upper() if response_hex else None)
    return {"valid": ok, "expected": expected["expected"], "explanation": expected["explanation"],
            "citations": expected["citations"]}
