# UDS Diagnostics and Automated Test Generation Assistant (CS5)

TechPulse FY-26 Applied AI/ML Capstone · Ayan Mujawar · PRN 123B1B196 · PCCOE Pune

A fully local, retrieval-augmented assistant for diagnostic test engineers working with UDS
(ISO 14229 concepts) on a fictional Demo OEM Body Control Module **BCM-X1**. It answers
diagnostic questions **with citations**, **validates requests and expected responses with a
deterministic rule engine**, **generates positive and negative test cases**, keeps them in a
**DRAFT → APPROVED engineer review workflow**, and **exports approved cases to Python
(python-can + udsoncan) and CAPL** templates.

> Design principle: **the LLM proposes, deterministic UDS rules decide, the engineer approves.**
> No hex byte in an expected response is ever produced by the language model.

## Folder structure

| Folder | Contents |
|---|---|
| `Synopsis/` | Approved project synopsis (signed) |
| `Input_Data/` | Synthetic knowledge base: UDS service guide + NRC catalogue (`standard_uds`), Demo OEM spec (`oem_spec`), BCM-X1 ECU extract with 26 DIDs / 6 RIDs / 3 DTCs (`ecu_extract`), 40 requirements + 20 reviewed tests (`project_reqs`), Jinja2 export templates |
| `Code/` | Python package `uds_assist`, FastAPI backend, Streamlit UI, index builder, evaluation script, unit tests |
| `Model_Prompts_Config/` | `config.yaml` (model, embeddings, retrieval, paths), versioned prompts, model card |
| `Evaluation_Results/` | Ground-truth sets, per-run metrics/CSVs/sample outputs, `EVALUATION_SUMMARY.md` |
| `Documentation/` | Technical report (updated with measured results) and UI screenshots |
| `Video/` | Demo video `CS5_Approach_Video_Ayan_Mujawar_123B1B196.mp4` and its script |
| `Declarations/` | AI-tool usage and student declaration |

## Architecture

```
Streamlit UI (localhost:8501)            FastAPI (127.0.0.1:8000, /docs)
        \                                   /
         +-------- service layer (uds_assist.service) --------+
         |                                                     |
   Cited Q&A (qa.py)                        Test generation (generator.py)
   hybrid retrieval -> identifier guard     requirement -> retrieval -> LLM scenario plan (JSON)
   -> out-of-scope guard -> LLM JSON        -> guardrails (allowed + applicable categories,
   -> citation guardrail                       citations must be retrieved chunk IDs)
   -> rule-consistency guardrail
         |                                  -> coverage checklist fills gaps
         |                                  -> rule engine simulates every step on a virtual ECU
         v                                  -> DRAFT in SQLite -> engineer review -> export (Jinja2)
   Retrieval (retrieval.py)                        |
   FAISS IndexFlatIP (bge-small-en-v1.5)    Rule engine (rules.py): session/security matrix,
   + BM25 (rank_bm25), RRF fusion           SID+0x40, 7F SID NRC, NRC order, DID/RID whitelist,
   4 collections, 151 chunks                seed/key + lockout, Demo OEM rules
         |                                         |
   Ollama qwen3:4b (local, temp 0.1, JSON)   SQLite uds_assist.db (tests, reviews, audit, Q&A log)
```

## Setup (Windows, tested) – everything runs locally, no cloud API

1. **Python 3.11+** environment:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install torch --index-url https://download.pytorch.org/whl/cpu
   pip install -r Code/requirements.txt
   ```
2. **Ollama** (https://ollama.com) and the model (2.5 GB):
   ```bash
   ollama pull qwen3:4b
   ```
3. **Build the index** (downloads `BAAI/bge-small-en-v1.5`, ~130 MB, once):
   ```bash
   cd Code
   python build_index.py
   ```
4. **Run**:
   ```bash
   streamlit run app.py                                  # UI  -> http://localhost:8501
   uvicorn api:app --host 127.0.0.1 --port 8000          # API -> http://127.0.0.1:8000/docs
   python -m pytest -q                                   # 22 unit tests
   python evaluate.py                                    # full evaluation (or --no-llm)
   ```
   Docker alternative: `docker compose up --build` (api, ui, ollama services; written for
   reproducibility but not executed for this submission).

**External dependencies (declared):** internet is needed only once, to download the Python
packages, the Ollama model `qwen3:4b` and the embedding model. At run time the system is
fully offline. If Ollama is not running, Q&A falls back to cited extractive answers and test
generation falls back to the deterministic checklist, so the demo never depends on the LLM.

## Model / RAG configuration

| Component | Choice |
|---|---|
| LLM | Qwen3-4B (`qwen3:4b`, Q4_K_M, Apache-2.0) via Ollama 0.40, temperature 0.1, num_ctx 4096, JSON format, thinking disabled |
| Embeddings | `BAAI/bge-small-en-v1.5` (384-dim, sentence-transformers, CPU) with query instruction; fallback `all-MiniLM-L6-v2` |
| Vector store | FAISS `IndexFlatIP` on L2-normalised vectors (cosine), one index per collection |
| Keyword index | BM25Okapi over all chunks with hex normalisation (`0x22`, `22h`, `$22` → `0x22`) |
| Fusion | Reciprocal-rank fusion (k = 60) of one global dense list + one global BM25 list, top-6, max 3 chunks per collection |
| Chunking | One table row = one chunk (DID, RID, service, NRC, requirement, test); prose split by heading, 400-token windows / 60 overlap |
| Prompts | `Model_Prompts_Config/prompts/qa_answer_v1.txt`, `scenario_plan_v1.txt` (version stored with every output) |
| Storage | SQLite: test cases + review status, audit log, Q&A log |

## Evaluation

Submitted run `run_20261023_122136` (qwen3:4b, 452 s):

| Area | Result |
|---|---|
| Retrieval, 27 in-scope questions | hybrid Hit@1 0.852 / Hit@3 0.926 / MRR 0.892 vs dense MRR 0.878 vs BM25 MRR 0.822 |
| Cited Q&A, 30 questions | 100 % grounded with valid citations, citation relevance 96.3 %, keyword recall 0.969 (25/27 fully correct), 3/3 unknown-ID / off-topic correctly declined, 0 false abstentions, JSON valid 100 %, median 9.6 s |
| Rule engine, 20 labelled pairs | 100 % validity accuracy, 100 % exact expected-response match |
| Test generation, 10 requirements | LLM alone: scenario recall 0.38, precision 1.0 → after rule checklist: recall 1.0, precision 1.0; 4 inapplicable LLM scenarios and 16 invalid citations removed by guardrails; median 11.6 s |

Failures and findings (reported honestly): Q04 answer omitted the NRC code 0x36 (said "blocks
and locks out for 10 s"); Q16 listed only 2 of the 3 extended-session routines; the small LLM
proposes mainly positive/session/security scenarios and misses length and unsupported-ID
negatives, which is why the deterministic coverage checklist is part of the design; in manual
testing the LLM stated NRC 0x31 instead of 0x33 for a locked write, which led to the
rule-consistency guardrail.

See `Evaluation_Results/EVALUATION_SUMMARY.md` for the full run (retrieval ablation
dense vs BM25 vs hybrid, cited Q&A metrics, rule-engine accuracy on 20 hand-labelled
request/response cases, LLM scenario recall/precision against an expert checklist for 10
requirements). Ground-truth sets are in `Evaluation_Results/ground_truth/`.

## Responsible AI

* **Grounding:** answers only from retrieved chunks; citations that are not retrieved chunk IDs are removed; an answer without a valid citation is replaced by the cited evidence.
* **No byte hallucination:** identifiers not in the approved ECU extract are refused by a deterministic guard; all request/response bytes come from the rule engine.
* **Rule-consistency guardrail:** when a question names a known DID, the rule engine computes the read/write outcome for every session/security state and gives it to the LLM. If the answer still states an NRC or byte that contradicts the scenario asked about, the rule engine corrects the answer and the UI flags it (found during testing: Qwen3-4B answered 0x31 instead of 0x33 for a locked write).
* **Out-of-scope guard:** questions far from the knowledge base (dense similarity < 0.55) are declined.
* **Human oversight:** every generated test is DRAFT; only a named reviewer can approve; only APPROVED tests are exported; every action is written to the audit log.
* **Privacy & security:** local LLM, local embeddings, synthetic data only (no ISO text, no OEM documents, no real VINs), API and UI bound to localhost, Streamlit telemetry disabled.
* **Safety:** exported scripts are templates for an approved bench / HIL environment only (Demo OEM spec section 8).

## Limitations

Synthetic knowledge base for one fictional ECU; no live CAN/DoIP execution (scripts are
generated, not run); timing requirements (P2, S3) are checked as documented values, not
measured; a 4B model on a laptop needs ~10–40 s per generation; the coverage checklist and the
expert checklist were written by the same person.
