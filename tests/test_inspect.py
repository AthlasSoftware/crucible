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
