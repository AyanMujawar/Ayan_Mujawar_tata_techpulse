"""Unit tests for the deterministic UDS rule engine (run: python -m pytest -q)."""
import pytest

from uds_assist.generator import applicable_categories, generate
from uds_assist.config import requirements
from uds_assist.rules import VirtualECU, parse_hex, pattern_matches, validate_request


def expected(req, **state):
    return validate_request(req, **state)["expected"]


def test_parse_hex_formats():
    assert parse_hex("22 F1 90") == parse_hex("0x22 0xF1 0x90") == parse_hex("22F190") == bytes([0x22, 0xF1, 0x90])
    with pytest.raises(ValueError):
        parse_hex("2G")


def test_read_vin_positive():
    assert expected("22 F1 90") == "62 F1 90 57 44 45 4D 4F 30 42 43 4D 58 31 30 30 30 30 30 31"


def test_positive_sid_is_request_plus_0x40():
    for req, sid in (("10 03", "50"), ("3E 00", "7E"), ("14 FF FF FF", "54"), ("11 01", "51")):
        assert expected(req).startswith(sid)


@pytest.mark.parametrize("req,state,nrc", [
    ("2E 4A 10 00 3C", {}, "7F"),                                         # service not in default session
    ("2E 4A 10 00 3C", {"session": "extended"}, "33"),                    # security locked
    ("2E 4A 10", {"session": "extended", "unlocked": [1]}, "13"),         # missing data byte
    ("2E 4A 10 01 2D", {"session": "extended", "unlocked": [1]}, "31"),   # 301 s > max 300 s
    ("2E F1 90 " + "00 " * 17, {"session": "extended", "unlocked": [1]}, "31"),  # read-only DID
    ("22 F1 A5", {}, "31"),                                               # unknown DID
    ("22 4A 30", {}, "31"),                                               # DID only readable in extended
    ("22 4A 61", {"session": "extended"}, "33"),                          # protected read
    ("27 02 00 00 00 00", {"session": "extended"}, "24"),                 # key without seed
    ("10 02", {}, "22"),                                                  # Demo OEM: default -> programming forbidden
    ("10 04", {}, "12"),                                                  # unsupported sub-function
    ("31 03 50 01", {"session": "extended"}, "24"),                       # results before start
    ("23 00 00", {}, "11"),                                               # service not supported
])
def test_negative_responses(req, state, nrc):
    assert expected(req, **state).split()[-1] == nrc


def test_security_lockout_sequence():
    ecu = VirtualECU(session="extended")
    codes = []
    for k in (1, 2, 3):
        ecu.process(parse_hex("27 01"))
        codes.append(ecu.process(bytes([0x27, 0x02, 0, 0, 0, k])).nrc)
    assert codes == [0x35, 0x35, 0x36]
    assert ecu.process(parse_hex("27 01")).nrc == 0x37


def test_valid_key_unlocks_and_session_change_relocks():
    ecu = VirtualECU(session="extended")
    seed = int.from_bytes(parse_hex(ecu.process(parse_hex("27 01")).response)[2:], "big")
    key = (seed ^ 0x5A3C96E1).to_bytes(4, "big")
    assert ecu.process(bytes([0x27, 0x02]) + key).expected == "67 02"
    assert 1 in ecu.unlocked
    ecu.process(parse_hex("10 03"))
    assert not ecu.unlocked


def test_suppress_positive_response():
    assert expected("3E 80", session="extended") == "<none>"


def test_pattern_matching():
    assert pattern_matches("67 01 XX XX XX XX", "67 01 12 34 56 78")
    assert not pattern_matches("67 01 XX XX XX XX", "67 01 12")
    assert pattern_matches("<none>", None)


def test_rule_consistency_corrects_wrong_nrc():
    from uds_assist.qa import did_fact_table, rule_consistency
    q = "Which NRC do I get if I write DID 0x4A10 in the extended session without unlocking security?"
    check = rule_consistency("NRC 0x31", q, did_fact_table(0x4A10))
    assert check["row"]["expected"] == "7F 2E 33" and check["conflict"] == ["31"]
    assert rule_consistency("NRC 0x33 securityAccessDenied", q, did_fact_table(0x4A10))["conflict"] == []


def test_generator_rule_only_covers_checklist():
    for rid, req in requirements().items():
        g = generate(rid, use_llm=False)
        assert {c["category"] for c in g["cases"]} == set(applicable_categories(req))
        for case in g["cases"]:
            for step in case["steps"]:
                if step["purpose"].startswith("precondition"):
                    assert not step["expected"].startswith("7F"), (case["id"], step)
            assert case["expected_final"].startswith("7F") == (case["category"] != "positive"), case["id"]
