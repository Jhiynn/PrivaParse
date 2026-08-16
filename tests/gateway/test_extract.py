from __future__ import annotations

import json

import pytest

from privaparse.gateway.adapter.openai import extract_answer, extract_request
from privaparse.gateway.extract import UnscannableField, write_back


def test_string_content_is_found():
    body = {"messages": [{"role": "user", "content": "Max Mustermann"}]}
    assert [n.text for n in extract_request(body)] == ["Max Mustermann"]


def test_array_content_finds_every_text_part():
    body = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "erste"},
        {"type": "text", "text": "zweite"},
    ]}]}
    assert [n.text for n in extract_request(body)] == ["erste", "zweite"]


def test_tool_call_arguments_are_walked_as_json():
    body = {"messages": [{"role": "assistant", "tool_calls": [
        {"id": "1", "type": "function", "function": {
            "name": "send", "arguments": '{"to": "max@test.de", "count": 3}'}}
    ]}]}
    found = [n.text for n in extract_request(body)]
    assert "max@test.de" in found


def test_a_number_leaf_in_tool_arguments_is_walked_too():
    """A phone number arrives as a JSON number often enough to matter."""
    body = {"messages": [{"role": "assistant", "tool_calls": [
        {"id": "1", "type": "function", "function": {
            "name": "dial", "arguments": '{"number": 4917012345}'}}
    ]}]}
    assert "4917012345" in [n.text for n in extract_request(body)]


def test_a_boolean_leaf_is_not_scanned():
    """`True` is an `int` in Python, so an unguarded number branch would send
    the string "True" to the detector and pseudonymise a flag."""
    body = {"messages": [{"role": "assistant", "tool_calls": [
        {"id": "1", "type": "function", "function": {
            "name": "send", "arguments": '{"urgent": true, "draft": false}'}}
    ]}]}
    assert extract_request(body) == []


def test_json_keys_are_not_scanned():
    """Parameter names come from the client's schema, not from a user."""
    body = {"messages": [{"role": "assistant", "tool_calls": [
        {"id": "1", "type": "function", "function": {
            "name": "send", "arguments": '{"Max Mustermann": "value"}'}}
    ]}]}
    assert [n.text for n in extract_request(body)] == ["value"]


def test_an_unknown_field_carrying_a_string_is_refused():
    body = {"messages": [{"role": "user", "content": "hallo"}],
            "some_new_field": "Max Mustermann"}
    with pytest.raises(UnscannableField) as excinfo:
        extract_request(body)
    assert "some_new_field" in str(excinfo.value)


def test_an_image_part_is_refused():
    body = {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR"}}
    ]}]}
    with pytest.raises(UnscannableField):
        extract_request(body)


def test_a_non_text_part_is_refused_even_when_it_carries_no_string():
    """The part type is the rule, not the payload.

    `test_an_image_part_is_refused` passes even with the type check removed,
    because the base64 URL trips the unknown-field rule instead. A part whose
    payload is all numbers has nothing to trip it, and the detector still
    cannot read a syllable of the audio it points at.
    """
    body = {"messages": [{"role": "user", "content": [
        {"type": "input_audio", "input_audio": {"sample_rate": 16000}}
    ]}]}
    with pytest.raises(UnscannableField):
        extract_request(body)


def test_known_non_text_fields_are_ignored_without_complaint():
    body = {"model": "gpt-4o", "temperature": 0.7, "stream": True,
            "messages": [{"role": "user", "content": "hallo"}]}
    assert [n.text for n in extract_request(body)] == ["hallo"]


def test_a_tools_declaration_is_forwarded_without_being_scanned():
    """A tool declaration is the client's own schema -- a function name, a
    description of what it does, a parameter shape. None of it is anything a
    user typed, and pseudonymising a tool's description would degrade the
    model's choice of tool without protecting a person. Same rule, and the
    same reason, as `response_format`.

    Refusing it instead is not the safe option it looks like: it makes the
    gateway unusable with every agent and IDE that declares tools, which sends
    those users back to talking to the provider directly.
    """
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hallo"}],
        "tools": [
            {"type": "function", "function": {
                "name": "send_mail",
                "description": "Send a mail to a recipient",
                "parameters": {"type": "object", "properties": {"to": {"type": "string"}}},
            }}
        ],
    }
    assert [n.text for n in extract_request(body)] == ["hallo"]


def test_write_back_leaves_a_tools_declaration_exactly_as_it_arrived():
    body = {
        "messages": [{"role": "user", "content": "Max Mustermann"}],
        "tools": [{"type": "function", "function": {"name": "send", "description": "Send"}}],
    }
    out = write_back(body, extract_request(body), ["[[PERSON_A1]]"])
    assert out["tools"] == body["tools"]


def test_write_back_round_trips_and_reserialises_tool_arguments():
    body = {"messages": [{"role": "assistant", "tool_calls": [
        {"id": "1", "type": "function", "function": {
            "name": "send", "arguments": '{"to": "max@test.de"}'}}
    ]}]}
    nodes = extract_request(body)
    out = write_back(body, nodes, ["[[EMAIL_A1]]"])
    assert json.loads(
        out["messages"][0]["tool_calls"][0]["function"]["arguments"]
    )["to"] == "[[EMAIL_A1]]"
    # The original must not be mutated: a failure downstream has to leave the
    # caller's body untouched.
    assert "max@test.de" in body["messages"][0]["tool_calls"][0]["function"]["arguments"]


# --- the refusal rule, at depth -------------------------------------------


def test_an_unknown_field_nested_inside_a_message_is_refused():
    """The walk refuses at any depth, not only at the top level."""
    body = {"messages": [
        {"role": "user", "content": "hallo", "some_future_field": "Max Mustermann"}
    ]}
    with pytest.raises(UnscannableField) as excinfo:
        extract_request(body)
    assert "some_future_field" in str(excinfo.value)


def test_the_pointer_of_a_refusal_locates_the_field():
    body = {"messages": [
        {"role": "user", "content": "hallo"},
        {"role": "user", "content": "hallo", "surprise": "Max Mustermann"},
    ]}
    with pytest.raises(UnscannableField) as excinfo:
        extract_request(body)
    assert excinfo.value.pointer == ("messages", 1, "surprise")


def test_a_person_written_into_a_tool_description_is_forwarded():
    """The cost of waving `tools` through, stated rather than discovered.

    A tool description is client-authored boilerplate in every case this
    gateway is built for, so it is not scanned -- but a client that writes a
    person into one sends that person to the provider. The alternative was
    refusing every request that declares a tool, which is every coding agent
    there is.
    """
    body = {"messages": [{"role": "user", "content": "hallo"}],
            "tools": [{"type": "function", "function": {
                "name": "send_mail", "description": "Send mail to Max Mustermann"}}]}

    nodes = extract_request(body)

    assert [n.text for n in nodes] == ["hallo"]
    assert "Max Mustermann" in write_back(body, nodes, ["hallo"])["tools"][0]["function"][
        "description"
    ]


def test_the_deprecated_functions_field_is_still_refused():
    """Same shape, same argument, but untested -- so it keeps the refusal a
    caller can see rather than an allowance nobody checked."""
    body = {"messages": [{"role": "user", "content": "hallo"}],
            "functions": [{"name": "send_mail", "description": "Send mail"}]}
    with pytest.raises(UnscannableField):
        extract_request(body)


def test_a_string_hidden_under_an_ignored_field_is_still_ignored():
    """`response_format` carries a schema of client-authored strings, not PII."""
    body = {"messages": [{"role": "user", "content": "hallo"}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "reply", "schema": {"type": "object"}}}}
    assert [n.text for n in extract_request(body)] == ["hallo"]


def test_an_empty_message_list_yields_nothing():
    assert extract_request({"model": "gpt-4o", "messages": []}) == []


def test_a_null_content_is_not_walked():
    """An assistant message that only calls a tool carries `content: null`."""
    body = {"messages": [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "1", "type": "function", "function": {
            "name": "ping", "arguments": "{}"}}
    ]}]}
    assert [n.text for n in extract_request(body)] == []


def test_a_participant_name_is_scanned_rather_than_waved_through():
    """`name` is client-supplied and holds a person often enough to matter."""
    body = {"messages": [{"role": "user", "name": "Max Mustermann", "content": "hallo"}]}
    assert [n.text for n in extract_request(body)] == ["Max Mustermann", "hallo"]


# --- write-back ------------------------------------------------------------


def test_write_back_replaces_plain_content():
    body = {"messages": [{"role": "user", "content": "Hallo Max"}]}
    out = write_back(body, extract_request(body), ["Hallo [[PERSON_A1]]"])
    assert out["messages"][0]["content"] == "Hallo [[PERSON_A1]]"
    assert body["messages"][0]["content"] == "Hallo Max"


def test_write_back_keeps_an_untouched_number_a_number():
    """Only a leaf that actually changed becomes a string."""
    body = {"messages": [{"role": "assistant", "tool_calls": [
        {"id": "1", "type": "function", "function": {
            "name": "dial", "arguments": '{"number": 4917012345, "count": 3}'}}
    ]}]}
    nodes = extract_request(body)
    replacements = ["[[PHONE_A1]]" if n.text == "4917012345" else n.text for n in nodes]
    out = write_back(body, nodes, replacements)
    arguments = json.loads(out["messages"][0]["tool_calls"][0]["function"]["arguments"])
    assert arguments["number"] == "[[PHONE_A1]]"
    assert arguments["count"] == 3


def test_write_back_refuses_a_replacement_count_that_does_not_match():
    """Misaligned replacements would silently scramble the request."""
    body = {"messages": [{"role": "user", "content": "hallo"}]}
    with pytest.raises(ValueError):
        write_back(body, extract_request(body), [])


def test_write_back_handles_two_nodes_in_one_arguments_string():
    body = {"messages": [{"role": "assistant", "tool_calls": [
        {"id": "1", "type": "function", "function": {
            "name": "send", "arguments": '{"to": "max@test.de", "body": "Max hier"}'}}
    ]}]}
    nodes = extract_request(body)
    out = write_back(body, nodes, ["[[EMAIL_A1]]", "[[PERSON_A1]] hier"])
    arguments = json.loads(out["messages"][0]["tool_calls"][0]["function"]["arguments"])
    assert arguments == {"to": "[[EMAIL_A1]]", "body": "[[PERSON_A1]] hier"}


def test_write_back_covers_every_node_extract_produced():
    """Extraction and write-back walk the same tree, so nothing is stranded."""
    body = {"model": "gpt-4o", "messages": [
        {"role": "system", "content": "Du bist hilfreich."},
        {"role": "user", "name": "Max", "content": [
            {"type": "text", "text": "erste"}, {"type": "text", "text": "zweite"}]},
        {"role": "assistant", "tool_calls": [
            {"id": "1", "type": "function", "function": {
                "name": "send", "arguments": '{"to": "max@test.de", "n": 2}'}}]},
    ]}
    nodes = extract_request(body)
    out = write_back(body, nodes, [f"<{i}>" for i in range(len(nodes))])
    serialised = json.dumps(out)
    for i in range(len(nodes)):
        assert f"<{i}>" in serialised


# --- the response side, which may never abort ------------------------------


def test_response_extraction_finds_content_and_tool_arguments():
    body = {"choices": [{"index": 0, "message": {
        "role": "assistant", "content": "Hallo [[PERSON_A1]]", "tool_calls": [
            {"id": "1", "type": "function", "function": {
                "name": "send", "arguments": '{"to": "[[EMAIL_A1]]"}'}}]},
        "finish_reason": "tool_calls"}]}
    assert [n.text for n in extract_answer(body)] == [
        "Hallo [[PERSON_A1]]", "[[EMAIL_A1]]"
    ]


def test_response_extraction_ignores_what_it_does_not_know():
    """The response path never aborts: a failure inbound shows a placeholder,
    so an unknown field is skipped rather than refused."""
    body = {"some_future_top_level": "x", "choices": [
        {"message": {"content": "ok", "some_future_field": "y"}}]}
    assert [n.text for n in extract_answer(body)] == ["ok"]


def test_response_extraction_survives_a_shape_it_did_not_expect():
    for body in ({}, {"choices": None}, {"choices": [None]},
                 {"choices": [{"message": None}]}, {"choices": "nonsense"}):
        assert extract_answer(body) == []


def test_a_restored_response_writes_back_through_the_same_machinery():
    body = {"choices": [{"message": {"role": "assistant", "content": "Hallo [[PERSON_A1]]"}}]}
    out = write_back(body, extract_answer(body), ["Hallo Max Mustermann"])
    assert out["choices"][0]["message"]["content"] == "Hallo Max Mustermann"
    assert body["choices"][0]["message"]["content"] == "Hallo [[PERSON_A1]]"
