"""Exports APPROVED test cases to Python (python-can + udsoncan) and CAPL via Jinja2 templates.
Requirement ID and citations are embedded as comments for traceability."""
from __future__ import annotations

import re
import time

from jinja2 import Environment, FileSystemLoader

from . import store
from .config import load_config, path


def _names(tc: dict) -> dict:
    base = re.sub(r"\W+", "_", tc["id"]).strip("_")
    return {**tc, "py_name": base.lower(), "capl_name": base, "reviewer": tc.get("reviewer") or "-",
            "reviewed_at": tc.get("reviewed_at") or "-"}


def export(fmt: str = "both", requirement_id: str | None = None, actor: str = "engineer") -> dict:
    tests = [_names(t) for t in store.list_tests(status="APPROVED", requirement_id=requirement_id)]
    if not tests:
        return {"files": [], "count": 0, "message": "No APPROVED test cases to export (DRAFTs are never exported)."}
    cfg = load_config()
    env = Environment(loader=FileSystemLoader(str(path("templates"))), trim_blocks=True, lstrip_blocks=True)
    ctx = {"ecu": cfg["project"]["ecu"], "tests": tests, "tx_id": cfg["ecu_bus"]["tx_id"], "rx_id": cfg["ecu_bus"]["rx_id"],
           "interface": cfg["ecu_bus"]["interface"], "channel": cfg["ecu_bus"]["channel"]}
    out_dir = path("exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    files = []
    if fmt in ("python", "both"):
        f = out_dir / f"bcm_x1_tests_{stamp}.py"
        f.write_text(env.get_template("python_udsoncan_test.py.j2").render(**ctx), encoding="utf-8")
        files.append(str(f))
    if fmt in ("capl", "both"):
        f = out_dir / f"bcm_x1_tests_{stamp}.can"
        f.write_text(env.get_template("capl_test.can.j2").render(**ctx), encoding="utf-8")
        files.append(str(f))
    store.audit("export", ",".join(t["id"] for t in tests), {"format": fmt, "files": files}, actor)
    return {"files": files, "count": len(tests), "message": f"Exported {len(tests)} approved test case(s)."}
