# AI-tool usage declaration and student declaration

Student: Ayan Mujawar · PRN 123B1B196 · PCCOE Pune · Case study CS5 – UDS Diagnostics and Automated Test Generation Assistant

## AI tools used

| AI tool / model | Purpose | Artifact affected | Verified by student |
|---|---|---|---|
| Claude (Anthropic), Claude Code | Code scaffolding and review, synthetic knowledge-base drafting, documentation, evaluation script | `Code/`, `Input_Data/`, `Model_Prompts_Config/`, `README.md`, synopsis, report | Y |
| Qwen3-4B via Ollama (local) | Runtime model inside the assistant: cited answers and test-scenario planning | Q&A answers, DRAFT test cases (always reviewed by an engineer before export) | Y |

## External / internet dependencies (declared)

* One-time downloads: Python packages (PyPI, PyTorch CPU index), Ollama installer, model `qwen3:4b` (registry.ollama.ai), embedding model `BAAI/bge-small-en-v1.5` (Hugging Face).
* At run time the system works offline; no cloud LLM API is used.

## Student declaration

I confirm that this submission represents my own understanding and contribution, that the work
follows the approved case-study scope (CS5), that only synthetic data is used, and that I can
explain every significant part of the implementation, its evaluation results and limitations.

The signed declaration is included in the synopsis (`Synopsis/`) and in the technical report (`Documentation/`).

Name: Ayan Mujawar  ·  PRN: 123B1B196
