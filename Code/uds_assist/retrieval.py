"""Hybrid retrieval: FAISS (bge-small, cosine) + BM25 per collection, fused with
reciprocal-rank fusion (RRF). Exact hex identifiers are caught by BM25, paraphrases by
the dense index."""
from __future__ import annotations

import functools
import json
import re
import time

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from .config import load_config, path
from .kb import chunk_dicts, normalise_hex


STOPWORDS = set("a an the is are was be to of in on for and or with what which how when why do does i "
                "it this that by as at from shall should can must after before if not any all my".split())


def tokenize(text: str) -> list[str]:
    text = normalise_hex(text).lower()
    text = re.sub(r"\b0x([0-9a-f]+)\b", r"\1", text)  # 0xF190 and F190 become the same token
    return [t for t in re.findall(r"[a-z0-9]+", text) if t not in STOPWORDS]


@functools.lru_cache(maxsize=1)
def embedder():
    from sentence_transformers import SentenceTransformer
    cfg = load_config()["embeddings"]
    try:
        return SentenceTransformer(cfg["model"], device="cpu"), cfg["model"]
    except Exception:  # offline fallback
        return SentenceTransformer(cfg["fallback_model"], device="cpu"), cfg["fallback_model"]


def embed(texts: list[str], query: bool = False) -> np.ndarray:
    cfg = load_config()["embeddings"]
    model, name = embedder()
    if query and "bge" in name:
        texts = [cfg["query_instruction"] + t for t in texts]
    vecs = model.encode(texts, normalize_embeddings=cfg["normalize"], batch_size=32, show_progress_bar=False)
    return np.asarray(vecs, dtype="float32")


def build_index() -> dict:
    """Embeds all chunks and writes one FAISS index + metadata file per collection."""
    out = path("index_dir")
    out.mkdir(parents=True, exist_ok=True)
    chunks = chunk_dicts()
    stats = {}
    for coll in load_config()["retrieval"]["collections"]:
        items = [c for c in chunks if c["collection"] == coll]
        vecs = embed([f"{c['title']}. {c['text']}" for c in items])
        index = faiss.IndexFlatIP(vecs.shape[1])
        index.add(vecs)
        faiss.write_index(index, str(out / f"{coll}.faiss"))
        (out / f"{coll}.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
        stats[coll] = len(items)
    (out / "index_meta.json").write_text(json.dumps({"embedding_model": embedder()[1], "chunks": stats,
                                                      "built": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=1))
    Retriever.instance.cache_clear()
    return stats


class Retriever:
    def __init__(self):
        cfg = load_config()["retrieval"]
        self.cfg = cfg
        self.collections = {}
        for coll in cfg["collections"]:
            items = json.loads((path("index_dir") / f"{coll}.json").read_text(encoding="utf-8"))
            self.collections[coll] = {"items": items, "faiss": faiss.read_index(str(path("index_dir") / f"{coll}.faiss"))}
        # one BM25 over the whole KB so IDF (and therefore scores) are comparable across collections
        self.all_items = [c for v in self.collections.values() for c in v["items"]]
        self.bm25 = BM25Okapi([tokenize(f"{c['title']} {c['text']}") for c in self.all_items])
        self.by_id = {c["chunk_id"]: c for c in self.all_items}

    @staticmethod
    @functools.lru_cache(maxsize=1)
    def instance() -> "Retriever":
        return Retriever()

    def search(self, query: str, collections: list[str] | None = None, k: int | None = None,
               mode: str = "hybrid") -> dict:
        """mode = hybrid | dense | bm25 (the last two are used for the ablation study)."""
        t0 = time.perf_counter()
        k = k or self.cfg["top_k"]
        n = self.cfg["per_list_k"]
        qvec = embed([query], query=True) if mode != "bm25" else None
        qtok = tokenize(query)
        wanted = collections or self.cfg["collections"]
        fused: dict[str, dict] = {}
        lists = []
        if mode in ("hybrid", "dense"):  # cosine scores are comparable, so merge into one global list
            dense = []
            for coll in wanted:
                c = self.collections[coll]
                scores, idx = c["faiss"].search(qvec, min(n, len(c["items"])))
                dense += [(c["items"][i]["chunk_id"], float(s)) for s, i in zip(scores[0], idx[0]) if i >= 0]
            lists.append(("dense", sorted(dense, key=lambda x: -x[1])[:n]))
        if mode in ("hybrid", "bm25"):
            bm = self.bm25.get_scores(qtok)
            order = [i for i in np.argsort(-bm) if bm[i] > 0 and self.all_items[i]["collection"] in wanted][:n]
            lists.append(("bm25", [(self.all_items[i]["chunk_id"], float(bm[i])) for i in order]))
        for kind, ranked in lists:
            for rank, (cid, score) in enumerate(ranked, start=1):
                hit = fused.setdefault(cid, {"rrf": 0.0, "dense": None, "bm25": None})
                hit["rrf"] += 1.0 / (self.cfg["rrf_k"] + rank)
                hit[kind] = max(score, hit[kind] or score)
        if mode == "dense":
            ordered = sorted(fused.items(), key=lambda kv: -(kv[1]["dense"] or 0))
        elif mode == "bm25":
            ordered = sorted(fused.items(), key=lambda kv: -(kv[1]["bm25"] or 0))
        else:
            ordered = sorted(fused.items(), key=lambda kv: -kv[1]["rrf"])
        cap = self.cfg.get("max_per_collection", k)
        picked, per_coll = [], {}
        for cid, sc in ordered:  # diversity cap: at most `cap` chunks per collection in the top-k
            coll = self.by_id[cid]["collection"]
            if per_coll.get(coll, 0) < cap:
                per_coll[coll] = per_coll.get(coll, 0) + 1
                picked.append((cid, sc))
        hits = []
        for cid, sc in picked[:k]:
            ch = self.by_id[cid]
            hits.append({**ch, "rrf": round(sc["rrf"], 5), "dense_score": None if sc["dense"] is None else round(sc["dense"], 4),
                         "bm25_score": None if sc["bm25"] is None else round(sc["bm25"], 3)})
        max_dense = max((h["dense_score"] or 0) for h in hits) if hits else 0.0
        return {"query": query, "mode": mode, "hits": hits, "max_dense": max_dense,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
