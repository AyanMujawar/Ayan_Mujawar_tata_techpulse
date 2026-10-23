# Demo OEM Diagnostic Specification (fictional)

Document ID: OEM-SPEC | Version 2.1 | Collection: oem_spec

This is a fictional OEM specification created for the project. It refines the generic UDS
rules for all ECUs of the fictional "Demo OEM" vehicle platform.

## 1 Addressing and transport

Diagnostics use ISO-TP on classical CAN at 500 kbit/s. Physical request CAN ID 0x7A0,
physical response CAN ID 0x7A8, functional request CAN ID 0x7DF. All test cases in this
project use physical addressing unless stated otherwise.

## 2 Session timing

P2 server max is 50 ms and is reported as 00 32 in the DiagnosticSessionControl positive
response. P2* server max is 5000 ms and is reported as 01 F4 (10 ms resolution). The S3
server timeout is 5000 ms: a tester must send TesterPresent (3E 00 or 3E 80) at least
every 2000 ms to keep the extended or programming session active.

## 3 Session transitions

Allowed transitions: default -> extended, extended -> programming, any session ->
default, extended -> extended. A direct transition from the default session to the
programming session is rejected with NRC 0x22 conditionsNotCorrect (Demo OEM rule; the
tester must enter the extended session first). Every session change locks all security
levels again.

## 4 Security access policy

Level 1 (sub-functions 0x01 / 0x02) is the "extended" level and is only available in the
extended session. Level 3 (sub-functions 0x03 / 0x04) is the "programming" level and is
only available in the programming session. Seeds and keys are 4 bytes long.

Demo key algorithm (for bench use only, not secret): key = seed XOR 0x5A3C96E1 for level 1
and key = seed XOR 0xA5C3691E for level 3.

Failed attempts: after 3 consecutive invalid keys the ECU answers the third invalid key
with NRC 0x36 exceededNumberOfAttempts and then refuses seed requests with NRC 0x37 for a
delay of 10 s. A valid key resets the attempt counter.

## 5 Negative response preferences

Demo OEM ECUs follow the generic NRC order with these clarifications:
- A known DID that is not readable or writable in the active session returns NRC 0x31,
  not 0x7F (0x7F is only used when the whole service is not allowed in the session).
- A write to a read-only DID returns NRC 0x31.
- A routine sub-function that the RID does not support returns NRC 0x12.
- Conditions such as vehicle speed > 3 km/h during writes or routines return NRC 0x22.

## 6 Data encoding rules

Multi-byte values are big-endian. Text DIDs (VIN, part numbers, system name) are ASCII.
Physical values use the scaling given in the ECU extract (for example battery voltage in
0.01 V steps, temperature with offset -40 degC).

## 7 Test case conventions

Each diagnostic requirement must be covered by at least one positive test and every
applicable negative category: wrong session, security locked, wrong length, value out of
range and request sequence. Test cases must state preconditions (session, security level,
vehicle state), the request bytes, the expected response bytes and the pass/fail
criterion. Only test cases approved by a diagnostic test engineer may be exported to the
automation framework.

## 8 Bench safety

Generated scripts are only executed on a HIL bench or bench ECU. ECUReset, routines that
actuate loads (lamps, door locks) and memory erase must never be run in a vehicle without
the bench safety checklist being completed.
