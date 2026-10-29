from osai import ollama_setup as o

DOLPHIN = {"capabilities": ["completion"], "details": {"family": "llama", "parameter_size": "8.0B"}}
QWEN_ABL = {"capabilities": ["completion", "tools", "vision", "thinking"], "details": {"family": "qwen35", "parameter_size": "9.0B"}}
QWEN_TINY = {"capabilities": ["completion", "tools"], "details": {"family": "qwen35", "parameter_size": "2B"}}
QWEN_BIG = {"capabilities": ["completion", "tools", "vision"], "details": {"family": "qwen35", "parameter_size": "27B"}}


def test_dolphin_llama3_is_rejected_for_lack_of_tools():
    r = o.assess("dolphin-llama3:latest", DOLPHIN, ram=16)
    assert not r["usable"] and "no tool calling" in r["problems"][0]


def test_picks_qwen_abliterated_and_flags_it(monkeypatch, capsys, tmp_path):
    shows = {"dolphin-llama3:latest": DOLPHIN, "huihui/qwen3.5-abliterated:9b": QWEN_ABL,
             "qwen3.5:2b": QWEN_TINY, "qwen3.5:27b": QWEN_BIG}
    monkeypatch.setattr(o, "_get", lambda p: {"models": [{"name": n} for n in shows]})
    monkeypatch.setattr(o, "_post", lambda p, b: shows[b["model"]])
    monkeypatch.setattr(o, "ram_gb", lambda: 16.0)
    monkeypatch.setenv("OSAI_HOME", str(tmp_path))
    import importlib, osai.config
    importlib.reload(osai.config)
    assert o.main([]) == 0
    out = capsys.readouterr().out
    assert "Chosen: huihui/qwen3.5-abliterated:9b" in out
    assert "too big for 16 GB" in out and "too small (2B)" in out and "never refuses" in out
    cfg = osai.config.Config(tmp_path / "config.json")
    assert cfg["provider"] == "local" and cfg["local"]["model"] == "huihui/qwen3.5-abliterated:9b"
    assert cfg["local"]["base_url"].endswith(":11434/v1")
