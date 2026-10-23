"""Ingests Input_Data, chunks it and (re)builds the FAISS + BM25 indexes.

Usage:  python build_index.py
"""
from uds_assist.retrieval import Retriever, build_index

if __name__ == "__main__":
    stats = build_index()
    print("Indexed chunks per collection:", stats, "| total", sum(stats.values()))
    hits = Retriever.instance().search("What is required to read the VIN on BCM-X1?")["hits"]
    print("Smoke test top-3:", [h["chunk_id"] for h in hits[:3]])
