# Demo video script (5–10 min)

Record the screen with Xbox Game Bar (Win + Alt + R) or OBS. Start Ollama, then `streamlit run app.py` in `Code/`.

| Time | Show | Say |
|---|---|---|
| 0:00–0:40 | README architecture block | Problem: UDS testing needs standard + OEM + ECU knowledge; my assistant answers with citations, a rule engine builds every byte, an engineer approves. Fully local. |
| 0:40–1:40 | `Input_Data/` folders, then **Knowledge base** tab (chunk chart, requirements per service, existing tests by category) | Synthetic knowledge base: service guide, 15 NRCs, Demo OEM spec, BCM-X1 extract (26 DIDs, 6 RIDs), 40 requirements, 20 existing tests – mostly positive, which is the coverage gap. 151 chunks, one table row per chunk. |
| 1:40–3:00 | **Ask** tab: "What is required to read the VIN on BCM-X1?" → expand retrieved evidence. Then "What does DID 0xF1A5 contain?" and "What is the capital of France?" | Hybrid FAISS + BM25 retrieval, cited answer with chunk IDs and rule-engine bytes. Unknown DID is refused by the identifier guard – no hallucinated length. Off-topic is declined. |
| 3:00–4:00 | **Validate request** tab: `2E 4A 10` extended + level 1 → 7F 2E 13; `2E 4A 10 00 3C` default → 7F 2E 7F; judge response `6E 4A 10` in default → WRONG | Deterministic rule engine: NRC order, session/security matrix, Demo OEM rules, cited evidence. |
| 4:00–5:30 | **Generate tests** tab: REQ-BCM-021 | LLM proposes scenario categories with cited rationale, guardrails filter them, checklist adds missing ones, rule engine simulates each step on the virtual ECU. All saved as DRAFT. |
| 5:30–6:30 | **Review & export**: approve 2–3 cases, export both, preview Python and CAPL, open audit log | Human oversight: only APPROVED cases export; reviewer, time, model and prompt version are logged. |
| 6:30–7:15 | **Coverage** tab | Coverage before/after approval; requirements without negative tests. |
| 7:15–8:30 | **Evaluation** tab / `EVALUATION_SUMMARY.md` | Retrieval ablation (hybrid best), Q&A grounded rate and citation validity, rule engine 20/20, LLM scenario recall vs expert checklist and how rule completion closes the gap. Mention failures honestly. |
| 8:30–9:00 | `pytest` output, FastAPI `/docs` | 22 unit tests; same features via REST API. Limitations: synthetic single ECU, no live CAN, small model latency. |
