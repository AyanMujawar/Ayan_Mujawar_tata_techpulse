"""Loads Model_Prompts_Config/config.yaml and resolves project paths."""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]  # project root (folder that holds Code/, Input_Data/ ...)


@functools.lru_cache(maxsize=1)
def load_config() -> dict:
    with open(ROOT / "Model_Prompts_Config" / "config.yaml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if os.environ.get("OLLAMA_URL"):  # docker-compose points the app at the ollama service
        cfg["llm"]["base_url"] = os.environ["OLLAMA_URL"]
    return cfg


def path(key: str) -> Path:
    return ROOT / load_config()["paths"][key]


def prompt(name: str) -> tuple[str, str]:
    """Returns (prompt_text, prompt_version) for a prompt key in config.prompts."""
    file_name = load_config()["prompts"][name]
    text = (path("prompts") / file_name).read_text(encoding="utf-8")
    return text, Path(file_name).stem


@functools.lru_cache(maxsize=1)
def ecu_extract() -> dict:
    with open(path("input_data") / "ecu_extract" / "bcm_x1_ecu_extract.json", encoding="utf-8") as fh:
        return json.load(fh)


@functools.lru_cache(maxsize=1)
def requirements() -> dict:
    with open(path("input_data") / "project_reqs" / "diagnostic_requirements.json", encoding="utf-8") as fh:
        return {r["id"]: r for r in json.load(fh)["requirements"]}


@functools.lru_cache(maxsize=1)
def nrc_names() -> dict[int, str]:
    with open(path("input_data") / "standard_uds" / "nrc_catalogue.json", encoding="utf-8") as fh:
        return {int(n["code"], 16): n["name"] for n in json.load(fh)["nrcs"]}
