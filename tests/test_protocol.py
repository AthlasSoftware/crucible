from crucible.sessions.protocol import (
    format_exec_result,
    parse_done,
    parse_fenced,
    system_prompt,
    truncate_for_model,
)


def test_parse_fenced_variants():
    assert parse_fenced("run this:\n```sh\nls -la\n```") == "ls -la"
    assert parse_fenced("```bash\necho hi\n```") == "echo hi"
    assert parse_fenced("```shell\npwd\n```") == "pwd"
    assert parse_fenced("```\nuname -a\n```") == "uname -a"


def test_parse_fenced_first_block_only():
    text = "```sh\nfirst\n```\nthen\n```sh\nsecond\n```"
    assert parse_fenced(text) == "first"


def test_parse_fenced_none():
    assert parse_fenced("no code here") is None
    assert parse_fenced("```sh\n\n```") is None  # empty block


def test_parse_fenced_multiline():
    text = "```sh\nfor i in 1 2 3; do\n  echo $i\ndone\n```"
    assert parse_fenced(text) == "for i in 1 2 3; do\n  echo $i\ndone"


def test_parse_done():
    assert parse_done("DONE: created the file") == "created the file"
    assert parse_done("some text\nDONE: all good") == "all good"
    assert parse_done("  DONE: indented") == "indented"
    assert parse_done("not DONE: inline mention") is None
    assert parse_done("nothing here") is None


def test_truncate_for_model():
    short = "x" * 100
    assert truncate_for_model(short) == short

    big = "a" * 5000 + "b" * 5000
    squeezed = truncate_for_model(big, head=1000, tail=1000)
    assert squeezed.startswith("a" * 1000)
    assert squeezed.endswith("b" * 1000)
    assert "8000 bytes truncated" in squeezed


def test_format_exec_result():
    out = format_exec_result(0, "hello\n", "", False)
    assert "exit code: 0" in out and "hello" in out

    out = format_exec_result(124, "", "", False)
    assert "timeout" in out and "(no output)" in out

    out = format_exec_result(1, "", "boom\n", True)
    assert "stderr" in out and "boom" in out and "capped" in out


def test_system_prompt_modes():
    native = system_prompt("native", "ubuntu:24.04", False, 60)
    fenced = system_prompt("fenced", "ubuntu:24.04", True, 30)
    assert "run_shell" in native and "fenced" not in native.lower()
    assert "```sh" in fenced
    assert "DISABLED" in native and "ENABLED" in fenced
