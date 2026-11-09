"""The agent's prompt-cache marks (agents/trucksim-llm/agent.py): where the breakpoints go on each call of a turn, that
the transcript stays unmarked, and how the cache's token counts are read."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location("trucksim_llm_agent",
                                               Path(__file__).resolve().parents[1] / "agents/trucksim-llm/agent.py")
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)

SYSTEM = {"role": "system", "content": "rules"}
USER = {"role": "user", "content": "turn 1, situation"}


def reply(*ids):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": i, "type": "function", "function": {"name": "list_loads", "arguments": "{}"}} for i in ids]}


def result(i):
    return {"role": "tool", "tool_call_id": i, "content": f"output {i}"}


def marked_at(messages):
    return [i for i, m in enumerate(agent._cache_marked(messages))
            if isinstance(m["content"], list) and m["content"][0].get("cache_control") == agent.CACHE_MARK]


def test_first_call_marks_the_system_prompt_and_the_situation():
    assert marked_at([SYSTEM, USER]) == [0, 1]


def test_a_later_call_also_marks_the_previous_calls_last_message():
    # call 2 re-sends call 1's prompt (up to the situation) plus the reply and its three tool results
    assert marked_at([SYSTEM, USER, reply("a", "b", "c"), result("a"), result("b"), result("c")]) == [0, 1, 5]
    # call 3: call 2's last message was the result of "c" (index 5)
    convo = [SYSTEM, USER, reply("a", "b", "c"), result("a"), result("b"), result("c"), reply("d"), result("d")]
    assert marked_at(convo) == [0, 5, 7]


def test_a_nudge_after_a_reply_without_tool_calls_is_marked_like_a_tool_result():
    nudge = {"role": "user", "content": "Act with the tools, and end the turn with end_turn(notes)."}
    assert marked_at([SYSTEM, USER, {"role": "assistant", "content": "thinking"}, nudge]) == [0, 1, 3]


def test_the_marks_keep_the_text_and_leave_the_transcript_unmarked():
    convo = [SYSTEM, USER, reply("a"), result("a")]
    before = [dict(m) for m in convo]
    sent = agent._cache_marked(convo)
    assert convo == before
    assert sent[3] == {"role": "tool", "tool_call_id": "a",
                       "content": [{"type": "text", "text": "output a", "cache_control": {"type": "ephemeral"}}]}
    assert sent[2] is convo[2]


def test_a_message_without_text_to_mark_fails_loudly():
    with pytest.raises(TypeError, match="no text content"):
        agent._cache_marked([SYSTEM, USER, reply("a")])


def test_cache_usage_reads_litellms_fields_and_counts_missing_ones_as_zero():
    assert agent._cache_usage(SimpleNamespace(cache_read_input_tokens=900, cache_creation_input_tokens=120)) == (900, 120)
    assert agent._cache_usage(SimpleNamespace(cache_read_input_tokens=None)) == (0, 0)
