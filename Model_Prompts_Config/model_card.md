# Model, embedding and pipeline configuration

| Item | Value |
|---|---|
| Runtime LLM | Qwen3-4B, Ollama tag `qwen3:4b` (digest 359d7dd4bcda, 2.5 GB, Q4_K_M), licence Apache-2.0 |
| Serving | Ollama 0.40.8 on localhost:11434, models stored on the local disk (`OLLAMA_MODELS`) |
| Hardware used | Intel i5-11300H, 24 GB RAM, NVIDIA RTX 3050 Laptop 4 GB (at num_ctx 8192 the model was split 55 % GPU / 45 % CPU) |
| Generation settings | temperature 0.1, num_ctx 4096 (reduced from the planned 8192 so the model fits fully on the 4 GB GPU), `format: json`, `think: false` (Qwen3 reasoning trace disabled) |
| Embedding model | `BAAI/bge-small-en-v1.5`, 384-dim, sentence-transformers 6.2.0 on CPU, query instruction prefix, L2-normalised |
| Vector store | FAISS 1.15.1 `IndexFlatIP` (exact cosine search), one index per collection under `Code/index/` |
| Keyword retrieval | rank_bm25 0.2.2 `BM25Okapi` over all 151 chunks, stop-word removal, hex normalisation |
| Fusion | Reciprocal-rank fusion, k = 60, 20 candidates per list, top-6, max 3 chunks per collection |
| Out-of-scope threshold | max dense cosine < 0.55 → declined (off-topic questions measured at 0.42–0.47, in-scope ≥ 0.6) |
| Prompts | `prompts/qa_answer_v1.txt` (cited Q&A), `prompts/scenario_plan_v1.txt` (scenario planning) |
| Tools / deterministic rules | `Code/uds_assist/rules.py` virtual BCM-X1: service/session matrix, NRC evaluation order, SID+0x40, 7F SID NRC, DID/RID whitelist and ranges, seed/key with 3-attempt lockout, Demo OEM rules (no default→programming, 0x31 for DID not in session) |
| Output traceability | every Q&A log row and test case stores model name, prompt version and cited chunk IDs |

## Why these choices

* **Qwen3-4B**: best instruction-following / JSON reliability in the 3–4B class that fits a 4 GB laptop GPU; Apache-2.0 licence; runs fully offline.
* **bge-small + BM25 hybrid**: dense retrieval handles paraphrases ("VIN", "vehicle identification number"); BM25 catches exact identifiers (`F190`, `0x7E`). The ablation in `Evaluation_Results` shows hybrid > dense > BM25 on MRR.
* **FAISS flat index**: 151 chunks → exact search is instant; no approximate index needed.
* **Rule engine for bytes**: protocol facts are deterministic and unit-testable, so the LLM is limited to retrieval-grounded explanation and scenario selection.
