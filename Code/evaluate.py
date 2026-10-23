"""Evaluation of the UDS assistant.

  1. Retrieval: Hit@1/3/6 and MRR on 27 in-scope questions, for dense / BM25 / hybrid (ablation).
  2. Q&A: grounded-answer rate, citation validity, citation relevance, keyword recall (faithfulness
     proxy), abstention on out-of-scope / unknown-identifier questions, latency.
  3. Rule engine: accuracy on 20 hand-labelled request/response pairs + exact expected-response match.
  4. Test generation: LLM scenario recall/precision vs an expert checklist (10 requirements),
     final coverage after rule completion, JSON validity, invalid-citation rate, latency.

Usage:  python evaluate.py            (full run, needs Ollama + qwen3:4b)
        python evaluate.py --no-llm   (retrieval + rules only)
Results go to Evaluation_Results/run_<timestamp>/ and EVALUATION_SUMMARY.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import time

from uds_assist import llm, qa
from uds_assist.config import load_config, path, requirements
from uds_assist.generator import generate
from uds_assist.retrieval import Retriever
from uds_assist.rules import check_pair, validate_request

GT = path("evaluation") / "ground_truth"


def load(name):
    return json.loads((GT / name).read_text(encoding="utf-8"))


def retrieval_eval(questions):
    r = Retriever.instance()
    out = {}
    for mode in ("dense", "bm25", "hybrid"):
        rows = []
        for q in questions:
            ids = [h["chunk_id"] for h in r.search(q["question"], mode=mode)["hits"]]
            rank = next((i + 1 for i, c in enumerate(ids) if c in q["relevant"]), None)
            rows.append({"id": q["id"], "rank": rank, "retrieved": ids})
        n = len(rows)
        out[mode] = {"hit@1": round(sum(1 for x in rows if x["rank"] == 1) / n, 3),
                     "hit@3": round(sum(1 for x in rows if x["rank"] and x["rank"] <= 3) / n, 3),
                     "hit@6": round(sum(1 for x in rows if x["rank"]) / n, 3),
                     "mrr": round(sum(1 / x["rank"] for x in rows if x["rank"]) / n, 3), "rows": rows}
    return out


def keyword_recall(answer: str, groups) -> float:
    if not groups:
        return 1.0
    a = answer.lower()
    return sum(any(alt in a for alt in g) for g in groups) / len(groups)


def qa_eval(questions):
    rows = []
    for q in questions:
        res = qa.answer(q["question"])
        abstained = res["source"] in ("identifier_guard", "out_of_scope_guard") or res.get("insufficient_evidence")
        rows.append({
            "id": q["id"], "type": q["type"], "question": q["question"], "answer": res["answer"],
            "source": res["source"], "citations": res["citations"], "rejected_citations": res["rejected_citations"],
            "citation_valid": res["citations_valid"], "citation_relevant": any(c in q["relevant"] for c in res["citations"]),
            "keyword_recall": round(keyword_recall(res["answer"], q["keywords"]), 3), "abstained": bool(abstained),
            "json_valid": res.get("json_valid"), "latency_s": res["latency_s"], "confidence": res.get("confidence")})
    ins = [r for r in rows if r["type"] == "in_scope"]
    oos = [r for r in rows if r["type"] != "in_scope"]
    lat = [r["latency_s"] for r in ins]
    return rows, {
        "in_scope_questions": len(ins),
        "grounded_answer_rate": round(sum(r["source"] in ("llm", "rule_engine_correction") and bool(r["citations"]) for r in ins) / len(ins), 3),
        "rule_engine_corrections": sum(r["source"] == "rule_engine_correction" for r in rows),
        "fallback_answers": sum(r["source"] in ("guardrail_fallback", "extractive_fallback") for r in ins),
        "citation_validity": round(sum(r["citation_valid"] for r in ins) / len(ins), 3),
        "citation_relevance": round(sum(r["citation_relevant"] for r in ins) / len(ins), 3),
        "keyword_recall_mean": round(statistics.mean(r["keyword_recall"] for r in ins), 3),
        "fully_correct_rate": round(sum(r["keyword_recall"] == 1.0 for r in ins) / len(ins), 3),
        "false_abstention_rate": round(sum(r["abstained"] for r in ins) / len(ins), 3),
        "abstention_accuracy_oos": round(sum(r["abstained"] for r in oos) / len(oos), 3) if oos else None,
        "json_valid_rate": round(sum(1 for r in ins if r["json_valid"]) / len(ins), 3),
        "latency_s_median": round(statistics.median(lat), 2), "latency_s_max": round(max(lat), 2)}


def rules_eval(cases):
    rows = []
    for c in cases:
        pred = check_pair(c["request"], c["response"], c["session"], c["unlocked"])
        exp = validate_request(c["request"], c["session"], c["unlocked"])["expected"]
        rows.append({"id": c["id"], "request": c["request"], "state": f"{c['session']} unlocked={c['unlocked']}",
                     "given_response": c["response"], "label_valid": c["valid"], "predicted_valid": pred["valid"],
                     "correct": pred["valid"] == c["valid"], "rule_expected": exp, "truth_expected": c["truth_expected"],
                     "expected_match": exp == c["truth_expected"]})
    n = len(rows)
    return rows, {"cases": n, "validity_accuracy": round(sum(r["correct"] for r in rows) / n, 3),
                  "expected_response_exact_match": round(sum(r["expected_match"] for r in rows) / n, 3)}


def generation_eval(checklist, use_llm):
    rows, kb_ids = [], set(Retriever.instance().by_id)
    for rid, expert in checklist.items():
        g = generate(rid, use_llm=use_llm)
        proposed, final = set(g["llm_proposed"]), {c["category"] for c in g["cases"]}
        expert = set(expert)
        cites = [c for case in g["cases"] for c in case["citations"]]
        rows.append({
            "requirement": rid, "expert": sorted(expert), "llm_proposed": sorted(proposed), "llm_rejected": g["llm_rejected"],
            "rule_added": g["rule_added"], "final": sorted(final),
            "llm_recall": round(len(proposed & expert) / len(expert), 3),
            "llm_precision": round(len(proposed & expert) / len(proposed), 3) if proposed else None,
            "final_recall": round(len(final & expert) / len(expert), 3),
            "final_precision": round(len(final & expert) / len(final), 3) if final else None,
            "cases": len(g["cases"]), "citations_exist_rate": round(sum(c in kb_ids for c in cites) / len(cites), 3),
            "invalid_llm_citations": sum(len(c["invalid_llm_citations"]) for c in g["cases"]),
            "json_valid": g["llm"].get("json_valid"), "latency_s": g["latency_s"],
            "schema_ok": all({"id", "requirement_id", "category", "steps", "expected_final", "citations", "status"} <= set(c) for c in g["cases"])})
    n = len(rows)
    prec = [r["llm_precision"] for r in rows if r["llm_precision"] is not None]
    return rows, {
        "requirements": n, "llm_scenario_recall_mean": round(statistics.mean(r["llm_recall"] for r in rows), 3),
        "llm_scenario_precision_mean": round(statistics.mean(prec), 3) if prec else None,
        "final_coverage_recall_mean": round(statistics.mean(r["final_recall"] for r in rows), 3),
        "final_precision_mean": round(statistics.mean(r["final_precision"] for r in rows), 3),
        "llm_json_valid_rate": round(sum(1 for r in rows if r["json_valid"]) / n, 3) if use_llm else None,
        "schema_compliance": round(sum(r["schema_ok"] for r in rows) / n, 3),
        "citation_existence_rate": round(statistics.mean(r["citations_exist_rate"] for r in rows), 3),
        "llm_rejected_scenarios": sum(len(r["llm_rejected"]) for r in rows),
        "invalid_llm_citations_dropped": sum(r["invalid_llm_citations"] for r in rows),
        "test_cases_generated": sum(r["cases"] for r in rows),
        "latency_s_median": round(statistics.median(r["latency_s"] for r in rows), 2)}


def write_csv(file, rows):
    with open(file, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in r.items()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()
    use_llm = not args.no_llm and llm.available()
    run_id = time.strftime("run_%Y%m%d_%H%M%S")
    out = path("evaluation") / run_id
    out.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    qs = load("qa_ground_truth.json")["questions"]
    t0 = time.time()

    print("[1/4] retrieval ablation ...")
    ret = retrieval_eval([q for q in qs if q["type"] == "in_scope"])
    print("[2/4] rule engine ...")
    rule_rows, rule_m = rules_eval(load("request_validation_cases.json")["cases"])
    qa_rows, qa_m, gen_rows, gen_m = [], None, [], None
    if use_llm:
        print("[3/4] Q&A with", cfg["llm"]["model"], "...")
        qa_rows, qa_m = qa_eval(qs)
    print("[4/4] test generation ...")
    gen_rows, gen_m = generation_eval(load("coverage_checklist.json")["requirements"], use_llm)

    metrics = {"run_id": run_id, "model": cfg["llm"]["model"] if use_llm else "none (rules/retrieval only)",
               "embedding_model": cfg["embeddings"]["model"], "top_k": cfg["retrieval"]["top_k"],
               "retrieval": {m: {k: v for k, v in d.items() if k != "rows"} for m, d in ret.items()},
               "qa": qa_m, "rule_engine": rule_m, "generation": gen_m, "duration_s": round(time.time() - t0, 1)}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    write_csv(out / "retrieval_results.csv", [{"mode": m, **r} for m, d in ret.items() for r in d["rows"]])
    write_csv(out / "rule_engine_results.csv", rule_rows)
    write_csv(out / "generation_results.csv", gen_rows)
    if qa_rows:
        write_csv(out / "qa_results.csv", qa_rows)
        with open(out / "sample_outputs.md", "w", encoding="utf-8") as fh:
            fh.write(f"# Sample outputs ({run_id}, {cfg['llm']['model']})\n\n")
            for r in qa_rows:
                fh.write(f"## {r['id']} ({r['type']}) - {r['question']}\n\n{r['answer']}\n\n"
                         f"- Citations: {', '.join(r['citations']) or 'none'}\n- Source: {r['source']} | keyword recall "
                         f"{r['keyword_recall']} | latency {r['latency_s']} s\n\n")
    summary = path("evaluation") / "EVALUATION_SUMMARY.md"
    with open(summary, "w", encoding="utf-8") as fh:
        fh.write(f"# Evaluation summary\n\nRun: `{run_id}` | LLM: {metrics['model']} | embeddings: {metrics['embedding_model']} | "
                 f"top-k {metrics['top_k']} | duration {metrics['duration_s']} s\n\n")
        fh.write("## Retrieval (27 in-scope questions)\n\n| Mode | Hit@1 | Hit@3 | Hit@6 | MRR |\n|---|---|---|---|---|\n")
        for m, d in metrics["retrieval"].items():
            fh.write(f"| {m} | {d['hit@1']} | {d['hit@3']} | {d['hit@6']} | {d['mrr']} |\n")
        for title, block in (("Cited Q&A (30 questions)", qa_m), ("Rule engine (20 request/response cases)", rule_m),
                             ("Test generation (10 requirements vs expert checklist)", gen_m)):
            if block:
                fh.write(f"\n## {title}\n\n| Metric | Value |\n|---|---|\n")
                for k, v in block.items():
                    fh.write(f"| {k} | {v} |\n")
        fh.write(f"\nFull per-item results: `{run_id}/`\n")
    print(json.dumps({k: metrics[k] for k in ("retrieval", "qa", "rule_engine", "generation")}, indent=1))
    print("Saved to", out)


if __name__ == "__main__":
    main()
