from crucible.benchmark.inspect import Finding, lint_ollama, render_inspect_md


def test_lint_missing_num_ctx():
    findings = lint_ollama({"template": "{{ .System }}{{ .Prompt }}",
                            "parameters": "", "model_context_length": 32768})
    bad = [f for f in findings if f.severity == "bad"]
    assert bad and "num_ctx" in bad[0].title
    assert "32768" in bad[0].detail


def test_lint_num_ctx_present():
    findings = lint_ollama({"template": "{{ .System }}{{ .Prompt }}",
                            "parameters": "num_ctx 8192"})
    assert any(f.severity == "ok" and "num_ctx" in f.title for f in findings)


def test_lint_tool_section():
    with_tools = lint_ollama({"template": "{{ .Tools }}{{ .System }}",
                              "parameters": "num_ctx 1"})
    assert any(f.severity == "ok" and "tool" in f.title for f in with_tools)
    without = lint_ollama({"template": "{{ .System }}{{ .Prompt }}",
                           "parameters": "num_ctx 1"})
    assert any(f.severity == "warn" and "NO tool" in f.title for f in without)


def test_lint_double_bos():
    findings = lint_ollama({"template": "<s>{{ .System }}", "parameters": "num_ctx 1"})
    assert any("BOS" in f.title for f in findings)


def test_render_inspect_md():
    md = render_inspect_md({
        "model": "test:8b", "backend": "ollama",
        "provenance": {"quant": "Q4_K_M", "template_sha256": "abc123"},
        "template": "{{ .Prompt }}", "parameters": "num_ctx 8192",
        "findings": [Finding("bad", "no num_ctx", "silent truncation").as_dict()],
    })
    assert "x-ray — test:8b" in md
    assert "✗ no num_ctx" in md
    assert "{{ .Prompt }}" in md


def test_kv_bytes_per_token():
    from crucible.benchmark.inspect import kv_bytes_per_token
    # llama3.1-8B: 32 layers, 8 kv-heads, head_dim 4096/32=128, f16
    info = {"n_layers": 32, "n_kv_heads": 8, "n_heads": 32, "embedding_length": 4096}
    assert kv_bytes_per_token(info) == 2 * 32 * 8 * 128 * 2  # 131072 B ≈ 0.125 MB
    # explicit head_dim wins
    assert kv_bytes_per_token({**info, "head_dim": 64}) == 2 * 32 * 8 * 64 * 2
    assert kv_bytes_per_token({}) is None


def test_context_budget():
    from crucible.benchmark.inspect import context_budget
    info = {"n_layers": 32, "n_kv_heads": 8, "n_heads": 32,
            "embedding_length": 4096, "model_context_length": 131072}
    # 24 GB free, 5 GB weights: (24e9*0.9 - 5e9*1.1) / 131072 B/tok ≈ 122k → capped 131072? no: 122k < 131k
    b = context_budget(info, 5_000_000_000, 24_000_000_000)
    assert b["kv_mb_per_1k"] == 134.2 or 130 < b["kv_mb_per_1k"] < 140
    assert 100_000 < b["practical_max_ctx"] <= 131072
    assert b["weights_fit"]
    # tiny free VRAM: weights don't even fit
    b2 = context_budget(info, 5_000_000_000, 2_000_000_000)
    assert not b2["weights_fit"] and b2["practical_max_ctx"] == 0
    # no free-vram info: only kv + trained
    b3 = context_budget(info, None, None)
    assert "practical_max_ctx" not in b3 and b3["trained_max_ctx"] == 131072
