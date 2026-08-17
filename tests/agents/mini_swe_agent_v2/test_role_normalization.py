"""Internal sentinel roles must not reach the Chat Completions API.

The agent loop uses ``role="exit"`` as loop bookkeeping.  ``_nudge_unsubmitted``
deliberately continues the episode past an exit message, so that message gets
replayed on the next request — and a strict provider (Azure / OpenAI) answers
with a 400 that kills the run, producing an empty patch from an agent that had
done real work.
"""

from __future__ import annotations

import pytest

from cooperbench.agents.mini_swe_agent_v2.models.litellm_model import (
    _API_ROLES,
    _normalize_internal_roles,
    _repair_dangling_tool_calls,
)


def test_exit_role_is_rewritten_to_user() -> None:
    out = _normalize_internal_roles([{"role": "exit", "content": "LimitsExceeded"}])
    assert out == [{"role": "user", "content": "LimitsExceeded"}]


def test_valid_roles_pass_through_untouched() -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
        {"role": "tool", "content": "t", "tool_call_id": "1"},
    ]
    assert _normalize_internal_roles(messages) == messages


def test_every_emitted_role_is_api_valid() -> None:
    """The property that actually matters, over a realistic transcript."""
    transcript = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "working"},
        {"role": "exit", "content": "submission text"},
        {"role": "user", "content": "you have not submitted anything"},
    ]
    assert all(m["role"] in _API_ROLES for m in _normalize_internal_roles(transcript))


def test_other_fields_and_order_are_preserved() -> None:
    out = _normalize_internal_roles([{"role": "exit", "content": "c", "name": "n"}])
    assert out[0]["content"] == "c"
    assert out[0]["name"] == "n"


def test_input_is_not_mutated() -> None:
    original = [{"role": "exit", "content": "c"}]
    _normalize_internal_roles(original)
    assert original[0]["role"] == "exit", "internal bookkeeping must survive"


@pytest.mark.parametrize("role", ["exit", "unknown_future_sentinel"])
def test_any_unknown_role_is_normalized(role: str) -> None:
    assert _normalize_internal_roles([{"role": role, "content": "x"}])[0]["role"] == "user"


# --- dangling tool calls -------------------------------------------------


def _assistant(*call_ids: str) -> dict:
    return {"role": "assistant", "tool_calls": [{"id": c, "type": "function"} for c in call_ids]}


def _tool(call_id: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": "output"}


def _unanswered(messages: list[dict]) -> set[str]:
    """Tool-call ids with no matching tool response anywhere after them."""
    answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    return {c["id"] for m in messages if m.get("role") == "assistant" for c in m.get("tool_calls", [])} - answered


def test_submission_sentinel_leaves_no_dangling_call() -> None:
    """The exact shape observed: assistant fires bash, environment raises
    Submitted instead of appending the result, nudge replays the history."""
    transcript = [
        _assistant("call_a"),
        _tool("call_a"),
        _assistant("call_b"),  # `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`
        {"role": "exit", "content": "submission"},
        {"role": "user", "content": "You have not submitted anything."},
    ]
    assert _unanswered(transcript) == {"call_b"}
    assert _unanswered(_repair_dangling_tool_calls(transcript)) == set()


def test_partially_answered_multi_call_is_completed() -> None:
    """One assistant message can carry several calls with only some answered."""
    transcript = [_assistant("call_a", "call_b"), _tool("call_a"), {"role": "exit", "content": ""}]
    repaired = _repair_dangling_tool_calls(transcript)
    assert _unanswered(repaired) == set()
    synthetic = [m for m in repaired if m.get("tool_call_id") == "call_b"]
    assert synthetic and "not recorded" in synthetic[0]["content"]


def test_well_formed_transcript_is_unchanged() -> None:
    transcript = [
        {"role": "system", "content": "s"},
        _assistant("call_a", "call_b"),
        _tool("call_a"),
        _tool("call_b"),
        {"role": "assistant", "content": "done"},
    ]
    assert _repair_dangling_tool_calls(transcript) == transcript


def test_repair_preserves_message_order() -> None:
    transcript = [_assistant("call_a"), {"role": "exit", "content": "x"}]
    roles = [m["role"] for m in _repair_dangling_tool_calls(transcript)]
    assert roles == ["assistant", "tool", "exit"], "tool reply must precede the exit message"


def test_assistant_without_tool_calls_is_untouched() -> None:
    transcript = [{"role": "assistant", "content": "just text"}, {"role": "user", "content": "u"}]
    assert _repair_dangling_tool_calls(transcript) == transcript


def test_both_repairs_compose_into_a_valid_payload() -> None:
    """End to end: the replayed history must satisfy both API constraints."""
    transcript = [
        _assistant("call_b"),
        {"role": "exit", "content": "submission"},
        {"role": "user", "content": "nudge"},
    ]
    prepared = _normalize_internal_roles(_repair_dangling_tool_calls(transcript))
    assert all(m["role"] in _API_ROLES for m in prepared)
    assert _unanswered(prepared) == set()
