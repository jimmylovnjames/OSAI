"""Point OSAI's free local brain at the best model already installed in Ollama.

    python -m osai.ollama_setup            # inspect, choose, write ~/.osai/config.json
    python -m osai.ollama_setup --dry-run  # just report

A model qualifies only if Ollama reports the "tools" capability (OSAI acts through tool calls).
Among those it prefers: Qwen3.5 family, vision support (OSAI looks at the screen), and the largest
size that fits comfortably in this computer's memory.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
if not OLLAMA.startswith("http"):
    OLLAMA = "http://" + OLLAMA


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _get(path: str) -> dict:
    with urllib.request.urlopen(OLLAMA + path, timeout=10) as r:
        return json.load(r)


def ram_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except (ValueError, OSError, AttributeError):
        return 8.0


def billions(size: str) -> float:
    m = re.match(r"([\d.]+)\s*([BM])", size or "", re.I)
    if not m:
        return 0.0
    return float(m.group(1)) / (1000 if m.group(2).upper() == "M" else 1)


def assess(name: str, show: dict, ram: float) -> dict:
    caps = set(show.get("capabilities") or [])
    details = show.get("details") or {}
    params = billions(details.get("parameter_size", ""))
    family = " ".join([details.get("family", ""), *(details.get("families") or []), name]).lower()
    need_gb = params * 0.75 + 1.5  # ~4-bit weights + context and runtime overhead
    problems = []
    if "tools" not in caps:
        problems.append("no tool calling (OSAI can't act with it)")
    if params and params < 3.5:
        problems.append(f"too small ({params:g}B) for reliable multi-step control")
    if params and need_gb > ram * 0.75:
        problems.append(f"too big for {ram:.0f} GB of memory")
    score = 0.0
    if not problems:
        score = 100 + params * 5 + (40 if "vision" in caps else 0) + (30 if "qwen3.5" in family or "qwen35" in family else 0)
    return {"name": name, "params_b": params, "tools": "tools" in caps, "vision": "vision" in caps,
            "usable": not problems, "problems": problems, "score": score,
            "abliterated": "abliterat" in name.lower() or "uncensored" in name.lower()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        models = [m["name"] for m in _get("/api/tags").get("models", [])]
    except Exception as e:
        print(f"Ollama isn't answering at {OLLAMA} ({e}). Open the Ollama app, then run this again.")
        return 2
    if not models:
        print("Ollama has no models yet. Suggested: a Qwen3.5 model with tool support (4B for 8 GB RAM, 9B for 16 GB+).")
        return 3
    ram = ram_gb()
    results = []
    for name in models:
        try:
            results.append(assess(name, _post("/api/show", {"model": name}), ram))
        except Exception as e:
            results.append({"name": name, "usable": False, "problems": [f"couldn't inspect: {e}"], "score": 0})
    print(f"Memory: {ram:.0f} GB")
    for r in sorted(results, key=lambda r: -r["score"]):
        verdict = "OK " if r["usable"] else "NO "
        extra = [f"{r.get('params_b', 0):g}B" if r.get("params_b") else "",
                 "vision" if r.get("vision") else "no vision"] if r["usable"] else r["problems"]
        print(f"  {verdict} {r['name']}: {', '.join(x for x in extra if x)}")
    best = max((r for r in results if r["usable"]), key=lambda r: r["score"], default=None)
    if not best:
        print("None of these can drive OSAI. Pull a model with tool support, e.g. a Qwen3.5 build, then rerun.")
        return 4
    print(f"\nChosen: {best['name']}")
    if not best["vision"]:
        print("  Note: no vision, so OSAI can't look at the screen to operate apps without other controls.")
    if best["abliterated"]:
        print("  Note: an uncensored model never refuses, including instructions hidden in web pages it reads.\n"
              "  Keep Settings -> Brain -> Autonomy on 'Ask' so risky actions still need your OK.")
    if args.dry_run:
        return 0
    from .config import Config
    cfg = Config()
    cfg.update({"provider": "local", "local": {"base_url": OLLAMA + "/v1", "model": best["name"],
                                               "vision": bool(best["vision"])}})
    print(f"OSAI now uses {best['name']} through Ollama (saved to {cfg.path}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
