"""The Responses API walk: what leaves the machine on `/v1/responses`.

A second protocol, not a second branch. The Responses API is not a renamed
Chat Completions: `messages` becomes `input`, which may be a bare string or a
list of items from a union with 32 members; the system prompt moves to a
top-level `instructions` string; and the answer arrives as `output[]` items
rather than `choices[]`.

The fail-closed rule is unchanged and matters more here, because that union
keeps growing. Four item types are walked -- the ones a coding agent actually
sends -- and anything else carrying a string stops the request.
"""

from __future__ import annotations

import pytest

from privaparse.gateway.adapter.responses import extract_input, extract_output
from privaparse.gateway.extract import UnscannableField, write_back


# --- the request -----------------------------------------------------------


def test_a_bare_string_input_is_found():
    assert [n.text for n in extract_input({"input": "Max Mustermann"})] == ["Max Mustermann"]


def test_instructions_are_scanned():
    """Codex puts its system prompt here, and a system prompt carries whatever
    the agent scraped into it."""
    body = {"instructions": "Der Kunde heisst Max Mustermann.", "input": "hallo"}
    assert [n.text for n in extract_input(body)] == [
        "Der Kunde heisst Max Mustermann.", "hallo",
    ]


def test_a_message_item_with_string_content_is_found():
    body = {"input": [{"type": "message", "role": "user", "content": "Max Mustermann"}]}
    assert [n.text for n in extract_input(body)] == ["Max Mustermann"]


def test_a_message_item_without_an_explicit_type_is_still_a_message():
    """`EasyInputMessageParam` makes `type` optional, so a role plus content is
    a message even with nothing saying so."""
    body = {"input": [{"role": "user", "content": "Max Mustermann"}]}
    assert [n.text for n in extract_input(body)] == ["Max Mustermann"]


def test_every_text_part_of_a_content_list_is_found():
    body = {"input": [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "erste"},
        {"type": "input_text", "text": "zweite"},
    ]}]}
    assert [n.text for n in extract_input(body)] == ["erste", "zweite"]


def test_an_assistant_output_text_part_is_found():
    """History replayed into a request carries `output_text`, not `input_text`."""
    body = {"input": [{"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": "Max Mustermann"},
    ]}]}
    assert [n.text for n in extract_input(body)] == ["Max Mustermann"]


def test_function_call_arguments_are_walked_as_json():
    body = {"input": [{"type": "function_call", "call_id": "c1", "name": "send",
                       "arguments": '{"to": "max@test.de"}'}]}
    assert "max@test.de" in [n.text for n in extract_input(body)]


def test_a_function_call_output_is_walked():
    """Tool results are where a codebase's contents enter the conversation."""
    body = {"input": [{"type": "function_call_output", "call_id": "c1",
                       "output": "Kontakt: max@test.de"}]}
    assert [n.text for n in extract_input(body)] == ["Kontakt: max@test.de"]


def test_a_reasoning_item_is_passed_over_without_complaint():
    """Encrypted reasoning is opaque and a summary is the model's own words
    about text already scanned elsewhere. Neither is a place a user types."""
    body = {"input": [
        {"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "gAAAA"},
        {"type": "message", "role": "user", "content": "hallo"},
    ]}
    assert [n.text for n in extract_input(body)] == ["hallo"]


def test_an_additional_tools_item_is_passed_over():
    """Tool definitions handed over mid-conversation, which a real Codex turn
    against a live model sends. Same content as the top-level `tools` field,
    so the same rule -- and the same cost: a person written into a tool
    description is forwarded.
    """
    body = {"input": [
        {"type": "additional_tools", "role": "developer", "id": "at_1", "tools": [
            {"type": "function", "name": "apply_patch", "description": "Edit a file"},
        ]},
        {"type": "message", "role": "user", "content": "Max Mustermann"},
    ]}
    assert [n.text for n in extract_input(body)] == ["Max Mustermann"]


def test_known_non_text_fields_are_ignored():
    body = {
        "model": "gpt-5-codex", "temperature": 0.2, "stream": True, "store": False,
        "max_output_tokens": 900, "previous_response_id": "resp_1",
        "reasoning": {"effort": "medium"}, "tool_choice": "auto",
        "parallel_tool_calls": True, "truncation": "auto",
        "tools": [{"type": "function", "name": "send", "description": "Send a mail"}],
        "input": "hallo",
    }
    assert [n.text for n in extract_input(body)] == ["hallo"]


def test_the_fields_a_real_codex_turn_sends_are_all_accounted_for():
    """Captured from Codex CLI 0.147.0 through a recording proxy, not guessed.

    `client_metadata` appears in no published schema; its contents are
    identifiers only -- installation, session, thread, turn and window ids
    plus a timestamp -- which is why it is waved through rather than scanned.
    """
    body = {
        "client_metadata": {
            "x-codex-turn-metadata": '{"installation_id":"a18f17ab","request_kind":"turn"}',
            "x-codex-installation-id": "a18f17ab", "turn_id": "019ff829",
            "thread_id": "019ff829", "x-codex-window-id": "019ff829:0",
            "session_id": "019ff829",
        },
        "include": ["reasoning.encrypted_content"],
        "instructions": "Du bist Codex.",
        "model": "qwen",
        "parallel_tool_calls": False,
        "prompt_cache_key": "019ff829",
        "reasoning": {"summary": "auto"},
        "store": False,
        "stream": True,
        "tool_choice": "auto",
        "tools": [{"type": "function", "name": "exec_command", "description": "Run"}],
        "input": [{"type": "message", "id": "msg_1", "role": "developer",
                   "content": [{"type": "input_text", "text": "Max Mustermann"}]}],
    }

    assert [n.text for n in extract_input(body)] == ["Du bist Codex.", "Max Mustermann"]


def test_an_unknown_top_level_field_carrying_text_is_refused():
    with pytest.raises(UnscannableField) as excinfo:
        extract_input({"input": "hallo", "some_new_field": "Max Mustermann"})
    assert "some_new_field" in str(excinfo.value)


def test_an_unknown_item_type_carrying_text_is_refused():
    """The input union has 32 members and grows. Anything not walked, stops."""
    body = {"input": [{"type": "computer_call", "action": {"text": "Max Mustermann"}}]}
    with pytest.raises(UnscannableField):
        extract_input(body)


def test_an_image_part_is_refused():
    body = {"input": [{"type": "message", "role": "user", "content": [
        {"type": "input_image", "image_url": "data:image/png;base64,iVBOR"},
    ]}]}
    with pytest.raises(UnscannableField):
        extract_input(body)


def test_write_back_round_trips_every_shape():
    body = {
        "instructions": "Kunde Max Mustermann",
        "input": [
            {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "erste"},
            ]},
            {"type": "function_call", "call_id": "c1", "name": "send",
             "arguments": '{"to": "max@test.de"}'},
        ],
    }
    nodes = extract_input(body)
    out = write_back(body, nodes, ["[[PERSON_A1]]", "[[X_A2]]", "[[EMAIL_A3]]"])

    import json
    assert out["instructions"] == "[[PERSON_A1]]"
    assert out["input"][0]["content"][0]["text"] == "[[X_A2]]"
    assert json.loads(out["input"][1]["arguments"])["to"] == "[[EMAIL_A3]]"
    # The caller's body is untouched.
    assert body["instructions"] == "Kunde Max Mustermann"


# --- the response ----------------------------------------------------------


def test_output_text_is_collected_from_the_output_array():
    reply = {"id": "resp_1", "object": "response", "output": [
        {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "Hallo [[PERSON_A1]]"},
        ]},
    ]}
    assert [n.text for n in extract_output(reply)] == ["Hallo [[PERSON_A1]]"]


def test_a_function_call_in_the_output_is_walked():
    reply = {"output": [
        {"type": "function_call", "call_id": "c1", "name": "send",
         "arguments": '{"to": "[[EMAIL_A1]]"}'},
    ]}
    assert "[[EMAIL_A1]]" in [n.text for n in extract_output(reply)]


def test_the_response_walk_never_raises():
    """Mirror of the chat side: a failure outbound risks disclosure, a failure
    inbound is at worst a visible placeholder."""
    for junk in [None, [], {"output": "not a list"}, {"output": [{"type": "???"}]}]:
        assert extract_output(junk) == []
