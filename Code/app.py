"""Streamlit engineering UI.  Run:  streamlit run app.py   ->  http://localhost:8501"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from uds_assist import service, store
from uds_assist.config import load_config, path, requirements
from uds_assist.retrieval import Retriever

st.set_page_config(page_title="UDS Test Assistant - BCM-X1", page_icon="🔧", layout="wide")
cfg = load_config()


@st.cache_resource(show_spinner="Loading indexes and embedding model ...")
def warm():
    Retriever.instance()
    return True


warm()
health = service.health()
with st.sidebar:
    st.markdown("### UDS Diagnostics & Test Generation Assistant")
    st.caption("CS5 · Demo OEM · ECU BCM-X1 (synthetic data)")
    st.write(f"**LLM:** `{cfg['llm']['model']}` via Ollama (local)")
    st.write("Status: " + ("🟢 running" if health["llm_available"] else "🔴 not reachable - extractive / rule-only fallback"))
    st.write(f"**Embeddings:** `{cfg['embeddings']['model']}`")
    st.write(f"**Retrieval:** FAISS + BM25, RRF, top-{cfg['retrieval']['top_k']}")
    reviewer = st.text_input("Reviewer name (for approvals)", value="Ayan Mujawar")
    st.info("LLM proposes · rule engine decides every byte · engineer approves.")

tabs = st.tabs(["💬 Ask", "🧪 Validate request", "⚙️ Generate tests", "✅ Review & export", "📊 Coverage",
                "📚 Knowledge base", "📈 Evaluation"])

# ------------------------------------------------------------------ Ask
with tabs[0]:
    st.subheader("Cited diagnostic Q&A")
    examples = ["What is required to read the VIN on BCM-X1?", "Which NRC do I get if I send a key without requesting a seed?",
                "Can I go directly from the default to the programming session?", "What does DID 0xF1A5 contain?"]
    ex = st.selectbox("Example questions", ["(type your own)"] + examples)
    q = st.text_input("Question", value="" if ex == "(type your own)" else ex)
    colls = st.multiselect("Collections", cfg["retrieval"]["collections"], default=cfg["retrieval"]["collections"])
    if st.button("Ask", type="primary") and q:
        with st.spinner("Retrieving evidence and asking the local LLM ..."):
            r = service.ask(q, colls)
        badge = {"llm": "🟢 grounded LLM answer", "identifier_guard": "🛡️ identifier guard", "out_of_scope_guard": "🛡️ out-of-scope guard",
                 "extractive_fallback": "🟠 extractive fallback (LLM offline)", "guardrail_fallback": "🟠 guardrail fallback (uncited LLM output rejected)",
                 "rule_engine_correction": "🛠️ LLM draft contradicted the rule engine - corrected"}
        st.markdown(f"**{badge.get(r['source'], r['source'])}** · confidence **{r.get('confidence')}** · {r['latency_s']} s")
        st.success(r["answer"])
        st.markdown("**Citations:** " + (" ".join(f"`{c}`" for c in r["citations"]) or "none"))
        if r["rejected_citations"]:
            st.warning(f"Guardrail removed citations not in the retrieved evidence: {r['rejected_citations']}")
        if r["guard"]["rule_facts"]:
            st.markdown("**Rule-engine facts (authoritative bytes, given to the LLM):**")
            st.dataframe(pd.DataFrame([{k: f[k] for k in ("scenario", "request", "expected", "nrc")} for f in r["guard"]["rule_facts"]]),
                         hide_index=True, use_container_width=True)
        with st.expander(f"Retrieved evidence ({len(r['retrieved'])} chunks, {r['retrieval_latency_ms']} ms)"):
            for h in r["retrieved"]:
                st.markdown(f"**[{h['chunk_id']}]** · {h['collection']} · `{h['source']}` v{h['version']} · "
                            f"rrf {h['rrf']} · dense {h['dense_score']} · bm25 {h['bm25_score']}")
                st.caption(h["text"])

# ------------------------------------------------------------------ Validate
with tabs[1]:
    st.subheader("Deterministic request / response validator")
    c1, c2, c3 = st.columns(3)
    req = c1.text_input("Request bytes", "2E 4A 10 00 3C")
    sess = c2.selectbox("Active session", ["default", "extended", "programming"])
    unl = c3.multiselect("Unlocked security levels", [1, 3])
    resp = st.text_input("Optional: response to judge (leave empty to compute the expected response)", "")
    if st.button("Validate", type="primary"):
        try:
            out = service.validate(req, sess, unl, None, resp or None)
        except ValueError as exc:
            st.error(str(exc))
        else:
            if resp:
                (st.success if out["valid"] else st.error)(f"Response is {'CORRECT' if out['valid'] else 'WRONG'} - expected `{out['expected']}`")
            elif out.get("valid_format") is False:
                st.error(out["error"])
            else:
                (st.success if out["positive"] else st.warning)(f"Expected response: `{out['expected']}`" +
                                                                (f"  ·  NRC {out['nrc']} {out['nrc_name']}" if out["nrc"] else ""))
            for line in out.get("explanation", []):
                st.write("• " + line)
            st.markdown("**Evidence:** " + " ".join(f"`{c}`" for c in out.get("citations", [])))

# ------------------------------------------------------------------ Generate
with tabs[2]:
    st.subheader("Generate test cases for a requirement")
    reqs = requirements()
    rid = st.selectbox("Requirement", list(reqs), index=list(reqs).index("REQ-BCM-021"),
                       format_func=lambda r: f"{r} - {reqs[r]['title']}")
    st.caption(reqs[rid]["text"])
    use_llm = st.toggle("Use LLM scenario planning", value=health["llm_available"])
    if st.button("Generate DRAFT test cases", type="primary"):
        with st.spinner("LLM planning scenarios, rule engine building bytes ..."):
            st.session_state["gen"] = service.generate_tests(rid, use_llm)
    g = st.session_state.get("gen")
    if g and g["requirement"]["id"] == rid:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Applicable scenarios", len(g["applicable"]))
        m2.metric("Proposed by LLM", len(g["llm_proposed"]))
        m3.metric("Added by rule checklist", len(g["rule_added"]))
        m4.metric("Latency", f"{g['latency_s']} s")
        if g["llm_rejected"]:
            st.warning(f"Guardrail rejected LLM scenarios: {g['llm_rejected']}")
        for c in g["cases"]:
            src = "🤖 LLM" if c["source"] == "llm" else "📐 rule completion"
            with st.expander(f"{c['id']} · {c['category']} · {src} · expected final `{c['expected_final']}` · {c['status']}"):
                st.write(f"**Rationale:** {c['rationale']}")
                st.write("**Preconditions:** " + "; ".join(c["preconditions"]))
                st.dataframe(pd.DataFrame([{"#": i + 1, "request": s["request"], "expected": s["expected"], "purpose": s["purpose"],
                                            "NRC": s["nrc_name"] or ""} for i, s in enumerate(c["steps"])]), hide_index=True, use_container_width=True)
                st.markdown("**Citations:** " + " ".join(f"`{x}`" for x in c["citations"]))
        st.info(f"Saved as DRAFT: {len(g['store']['saved'])} · already reviewed (kept): {len(g['store']['kept_reviewed'])}. "
                "Approve them in the Review tab before export.")

# ------------------------------------------------------------------ Review & export
with tabs[3]:
    st.subheader("Engineer review queue")
    status = st.radio("Show", ["DRAFT", "APPROVED", "REJECTED"], horizontal=True)
    tests = store.list_tests(status=status)
    st.caption(f"{len(tests)} test case(s)")
    for t in tests:
        with st.expander(f"{t['id']} · {t['requirement_id']} · {t['category']} · {t.get('source')} · final `{t['expected_final']}`"):
            st.dataframe(pd.DataFrame([{"request": s["request"], "expected": s["expected"], "purpose": s["purpose"]} for s in t["steps"]]),
                         hide_index=True, use_container_width=True)
            st.markdown("**Citations:** " + " ".join(f"`{x}`" for x in t["citations"]))
            if t.get("reviewer"):
                st.caption(f"Reviewed by {t['reviewer']} at {t['reviewed_at']}: {t.get('review_comment') or ''}")
            comment = st.text_input("Comment", key=f"c_{t['id']}")
            b1, b2, b3 = st.columns(3)
            if status != "APPROVED" and b1.button("Approve", key=f"a_{t['id']}"):
                service.review(t["id"], "approve", reviewer, comment)
                st.rerun()
            if status != "REJECTED" and b2.button("Reject", key=f"r_{t['id']}"):
                service.review(t["id"], "reject", reviewer, comment)
                st.rerun()
            if status != "DRAFT" and b3.button("Reopen", key=f"o_{t['id']}"):
                service.review(t["id"], "reopen", reviewer, comment)
                st.rerun()
    st.divider()
    st.subheader("Export APPROVED test cases")
    fmt = st.radio("Format", ["both", "python", "capl"], horizontal=True)
    if st.button("Export", type="primary"):
        out = service.export(fmt, None, reviewer)
        st.write(out["message"])
        for f in out["files"]:
            content = open(f, encoding="utf-8").read()
            st.download_button(f"Download {f.split(chr(92))[-1].split('/')[-1]}", content, file_name=f.split(chr(92))[-1].split("/")[-1])
            with st.expander(f"Preview {f.split(chr(92))[-1].split('/')[-1]}"):
                st.code(content[:4000], language="python" if f.endswith(".py") else "c")
    with st.expander("Audit log"):
        st.dataframe(pd.DataFrame(store.audit_entries(100)), hide_index=True, use_container_width=True)

# ------------------------------------------------------------------ Coverage
with tabs[4]:
    st.subheader("Requirement coverage (existing reviewed tests + APPROVED generated tests)")
    cov = service.coverage()
    k1, k2, k3 = st.columns(3)
    k1.metric("Scenario coverage", f"{cov['overall_pct']} %")
    k2.metric("Covered / applicable scenarios", f"{cov['covered_scenarios']} / {cov['applicable_scenarios']}")
    k3.metric("Requirements without any negative test", cov["requirements_without_negative_test"])
    df = pd.DataFrame([{"requirement": r["requirement"], "title": r["title"], "service": r["service"], "coverage %": r["coverage_pct"],
                        "missing": ", ".join(r["missing"]), "pending draft": ", ".join(r["pending_draft"])} for r in cov["rows"]])
    st.dataframe(df, hide_index=True, use_container_width=True)

# ------------------------------------------------------------------ Knowledge base / EDA
with tabs[5]:
    st.subheader("Knowledge base and EDA")
    kb = service.kb_stats()
    if kb["index"]:
        st.write(f"Indexed **{sum(kb['index']['chunks'].values())} chunks** with `{kb['index']['embedding_model']}` (built {kb['index']['built']})")
        st.bar_chart(pd.Series(kb["index"]["chunks"], name="chunks"))
    a, b = st.columns(2)
    a.markdown("**Requirements per UDS service**")
    a.bar_chart(pd.Series(kb["services_per_requirement"], name="requirements"))
    b.markdown("**Existing reviewed tests by category** (shows the negative-test gap)")
    b.bar_chart(pd.Series(kb["existing_tests_by_category"], name="tests"))
    st.write(f"DIDs: {kb['dids']} ({kb['dids_by_access']}), readable in default session: {kb['dids_readable_in_default']}; "
             f"writable DIDs by security level: {kb['dids_by_write_security']}; RIDs: {kb['rids']}; requirements: {kb['requirements']}")
    with st.expander("Browse chunks"):
        r = Retriever.instance()
        coll = st.selectbox("Collection", cfg["retrieval"]["collections"])
        st.dataframe(pd.DataFrame([{"chunk_id": c["chunk_id"], "title": c["title"], "text": c["text"][:200]} for c in r.collections[coll]["items"]]),
                     hide_index=True, use_container_width=True)

# ------------------------------------------------------------------ Evaluation
with tabs[6]:
    st.subheader("Evaluation results")
    summary = path("evaluation") / "EVALUATION_SUMMARY.md"
    if summary.exists():
        st.markdown(summary.read_text(encoding="utf-8"))
    runs = sorted(p for p in path("evaluation").glob("run_*") if (p / "metrics.json").exists())  # finished runs only
    if runs:
        metrics = json.loads((runs[-1] / "metrics.json").read_text())
        with st.expander(f"Raw metrics ({runs[-1].name})"):
            st.json(metrics)
        sample = runs[-1] / "sample_outputs.md"
        if sample.exists():
            with st.expander("Sample Q&A outputs with citations"):
                st.markdown(sample.read_text(encoding="utf-8"))
