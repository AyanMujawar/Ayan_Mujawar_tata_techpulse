# UDS Service Guide (synthetic, self-written)

Document ID: STD-GUIDE | Version 1.0 | Collection: standard_uds

This guide summarises Unified Diagnostic Services concepts in our own words for the
project knowledge base. It is NOT a copy of ISO 14229 text. Byte values follow the
publicly known UDS conventions.

## General message rules

Every UDS request starts with a one-byte Service Identifier (SID). A positive response
uses the response SID, which is always the request SID plus 0x40 (for example 0x22 becomes
0x62). A negative response is always exactly three bytes: 0x7F, the request SID, and a
Negative Response Code (NRC), for example 7F 22 31.

Services that have a sub-function carry it in the second byte. Bit 7 of the sub-function
byte (0x80) is the suppressPosRspMsgIndicationBit: when it is set and the request is
successful, the ECU sends no positive response. Negative responses are never suppressed
for physically addressed requests.

## NRC evaluation order

When several problems exist in one request, the ECU reports the first failing check in
this order:
1. Service not supported at all -> NRC 0x11.
2. Service not supported in the active session -> NRC 0x7F.
3. Message shorter than the minimum length for the service -> NRC 0x13.
4. Sub-function not supported -> NRC 0x12.
5. Sub-function not supported in the active session -> NRC 0x7E.
6. Identifier (DID / RID / group) not supported -> NRC 0x31.
7. Exact length or data format wrong -> NRC 0x13.
8. Identifier not accessible in the active session -> NRC 0x31.
9. Security access required but not unlocked -> NRC 0x33.
10. Wrong request sequence (for example key without seed) -> NRC 0x24.
11. Data value outside the allowed range -> NRC 0x31.
12. Other conditions not correct (for example vehicle speed) -> NRC 0x22.

## Service 0x10 DiagnosticSessionControl

Purpose: switch the diagnostic session. Sub-functions: 0x01 defaultSession,
0x02 programmingSession, 0x03 extendedDiagnosticSession. Request length is exactly 2 bytes
(10 03). Positive response: 50 + sub-function + P2 server max (2 bytes, 1 ms resolution) +
P2* server max (2 bytes, 10 ms resolution). Example: 10 03 -> 50 03 00 32 01 F4.
Changing session always re-locks security access. Unsupported sub-function -> NRC 0x12;
wrong length -> NRC 0x13.

## Service 0x11 ECUReset

Purpose: reset the ECU. Sub-functions: 0x01 hardReset, 0x03 softReset. Request length is
exactly 2 bytes. Positive response: 51 + sub-function. After a reset the ECU returns to the
default session and security is locked.

## Service 0x14 ClearDiagnosticInformation

Purpose: clear stored DTCs. Request: 14 + 3-byte groupOfDTC. FF FF FF means all groups.
Request length is exactly 4 bytes, otherwise NRC 0x13. Unsupported group -> NRC 0x31.
Positive response: 54 (single byte).

## Service 0x19 ReadDTCInformation

Purpose: read stored DTCs. Supported report types in this project: 0x01
reportNumberOfDTCByStatusMask (request 19 01 + status mask, 3 bytes), 0x02
reportDTCByStatusMask (19 02 + status mask, 3 bytes) and 0x0A reportSupportedDTC (19 0A,
2 bytes). Positive response starts with 59 + report type.

## Service 0x22 ReadDataByIdentifier

Purpose: read a data record identified by a 2-byte Data Identifier (DID). Request: 22 +
DID high byte + DID low byte (3 bytes for one DID). The request length must be 1 + 2*n.
Positive response: 62 + DID + data record whose length is defined per DID in the ECU
extract. Unknown DID -> NRC 0x31. DID not readable in the active session -> NRC 0x31.
DID protected and security locked -> NRC 0x33.

## Service 0x27 SecurityAccess

Purpose: unlock protected services with a seed/key exchange. Odd sub-functions request a
seed (0x01, 0x03 ...), the following even sub-function sends the key (0x02, 0x04 ...).
Request seed: 27 01 -> 67 01 + seed. If the level is already unlocked the seed is all
zeros. Send key: 27 02 + key -> 67 02. Sending a key without first requesting a seed ->
NRC 0x24 requestSequenceError. Wrong key -> NRC 0x35 invalidKey. Too many wrong keys ->
NRC 0x36 exceededNumberOfAttempts, after which seed requests are refused with NRC 0x37
requiredTimeDelayNotExpired until the delay timer expires.

## Service 0x28 CommunicationControl

Purpose: enable or disable normal message transmission/reception. Request: 28 +
controlType + communicationType (3 bytes). controlType 0x00 enableRxAndTx, 0x01
enableRxAndDisableTx, 0x03 disableRxAndTx. communicationType 0x01 normal messages.
Positive response: 68 + controlType.

## Service 0x2E WriteDataByIdentifier

Purpose: write a data record. Request: 2E + DID (2 bytes) + data record. The data length
must match the DID length exactly, otherwise NRC 0x13. Unknown or read-only DID -> NRC
0x31. Value outside the allowed range -> NRC 0x31. Positive response: 6E + DID (3 bytes).
Most writable DIDs are protected by security access.

## Service 0x31 RoutineControl

Purpose: start, stop or request results of a routine identified by a 2-byte Routine
Identifier (RID). Sub-functions: 0x01 startRoutine, 0x02 stopRoutine, 0x03
requestRoutineResults. Minimum length 4 bytes (31 01 + RID). Unknown RID -> NRC 0x31.
Stop or results before start -> NRC 0x24. Positive response: 71 + sub-function + RID +
optional status record.

## Services 0x34 / 0x36 / 0x37 data download (summary)

0x34 RequestDownload announces a download, 0x36 TransferData sends numbered blocks and
0x37 RequestTransferExit finishes the transfer. These services are only available in the
programming session with programming-level security unlocked. 0x36 or 0x37 without an
accepted 0x34 -> NRC 0x24. Positive responses: 74, 76 + block counter, 77.

## Service 0x3E TesterPresent

Purpose: keep a non-default session alive. Request: 3E 00 (2 bytes). Positive response:
7E 00. With the suppress bit (3E 80) the ECU stays silent. If no request is received for
the S3 server time (typically 5 s) the ECU falls back to the default session.

## Service 0x85 ControlDTCSetting

Purpose: stop or resume DTC status updates, for example during flashing. Sub-functions
0x01 on and 0x02 off. Request length 2 bytes. Positive response: C5 + sub-function.
