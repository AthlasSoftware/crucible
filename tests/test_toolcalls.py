from crucible.backends.base import ToolCall
from crucible.benchmark.toolcalls import CASES, score_case, wilson_interval


def _case(cid):
    return next(c for c in CASES if c.id == cid)


def test_single_call_pass():
    ok, _ = score_case(_case("select-basic"),
                       [ToolCall("1", "get_weather", {"city": "Paris, France"})])
    assert ok


def test_wrong_tool_fails():
    ok, reason = score_case(_case("select-basic"),
                            [ToolCall("1", "web_search", {"query": "paris weather"})])
    assert not ok and "web_search" in reason


def test_numeric_tolerance():
    ok, _ = score_case(_case("args-numbers"),
                       [ToolCall("1", "add_numbers", {"a": 847.0, "b": 256})])
    assert ok


def test_abstain_pass_and_fail():
    ok, _ = score_case(_case("abstain-chat"), [])
    assert ok
    ok, reason = score_case(_case("abstain-chat"),
                            [ToolCall("1", "web_search", {"query": "haiku"})])
    assert not ok and "abstain" in reason


def test_parallel_requires_both():
    calls = [ToolCall("1", "get_weather", {"city": "Oslo"}),
             ToolCall("2", "get_weather", {"city": "Madrid"})]
    ok, _ = score_case(_case("parallel-two"), calls)
    assert ok
    ok, _ = score_case(_case("parallel-two"), calls[:1])
    assert not ok


def test_extra_call_fails_single():
    calls = [ToolCall("1", "get_weather", {"city": "Paris"}),
             ToolCall("2", "get_weather", {"city": "Paris"})]
    ok, reason = score_case(_case("select-basic"), calls)
    assert not ok and "one call" in reason


def test_parallel_rejects_unrequested_call():
    calls = [ToolCall("1", "get_weather", {"city": "Oslo"}),
             ToolCall("2", "get_weather", {"city": "Madrid"}),
             ToolCall("3", "web_search", {"query": "unrequested"})]
    assert score_case(_case("parallel-two"), calls)[0] is False


def test_numeric_types_do_not_accept_strings_or_booleans():
    for value in ("847", True):
        calls = [ToolCall("1", "add_numbers", {"a": value, "b": 256})]
        assert score_case(_case("args-numbers"), calls)[0] is False


def test_wilson_interval():
    lo, hi = wilson_interval(30, 36)
    assert 0.65 < lo < 0.75 and 0.9 < hi < 0.95
    assert wilson_interval(0, 0) == (0.0, 0.0)
