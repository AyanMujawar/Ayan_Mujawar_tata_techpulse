# Evaluation summary

Run: `run_20261023_122136` | LLM: qwen3:4b | embeddings: BAAI/bge-small-en-v1.5 | top-k 6 | duration 452.0 s

## Retrieval (27 in-scope questions)

| Mode | Hit@1 | Hit@3 | Hit@6 | MRR |
|---|---|---|---|---|
| dense | 0.815 | 0.926 | 0.963 | 0.878 |
| bm25 | 0.741 | 0.889 | 0.926 | 0.822 |
| hybrid | 0.852 | 0.926 | 0.963 | 0.892 |

## Cited Q&A (30 questions)

| Metric | Value |
|---|---|
| in_scope_questions | 27 |
| grounded_answer_rate | 1.0 |
| rule_engine_corrections | 0 |
| fallback_answers | 0 |
| citation_validity | 1.0 |
| citation_relevance | 0.963 |
| keyword_recall_mean | 0.969 |
| fully_correct_rate | 0.926 |
| false_abstention_rate | 0.0 |
| abstention_accuracy_oos | 1.0 |
| json_valid_rate | 1.0 |
| latency_s_median | 9.55 |
| latency_s_max | 18.1 |

## Rule engine (20 request/response cases)

| Metric | Value |
|---|---|
| cases | 20 |
| validity_accuracy | 1.0 |
| expected_response_exact_match | 1.0 |

## Test generation (10 requirements vs expert checklist)

| Metric | Value |
|---|---|
| requirements | 10 |
| llm_scenario_recall_mean | 0.382 |
| llm_scenario_precision_mean | 1.0 |
| final_coverage_recall_mean | 1.0 |
| final_precision_mean | 1.0 |
| llm_json_valid_rate | 1.0 |
| schema_compliance | 1.0 |
| citation_existence_rate | 1.0 |
| llm_rejected_scenarios | 4 |
| invalid_llm_citations_dropped | 16 |
| test_cases_generated | 46 |
| latency_s_median | 11.61 |

Full per-item results: `run_20261023_122136/`
