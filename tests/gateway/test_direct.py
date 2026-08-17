"""The direct API: PrivaParse's own capabilities over HTTP, not a proxy."""

from __future__ import annotations

import sys

from starlette.testclient import TestClient

from privaparse.gateway.server import create_app


def test_detect_returns_a_span_per_text(direct_client):
    response = direct_client.post(
        "/privaparse/detect",
        json={"texts": ["Schreiben Sie an max@test.de", "nichts hier"]},
    )
    assert response.status_code == 200
    detections = response.json()["detections"]
    assert len(detections) == 2
    assert detections[0][0]["type"] == "EMAIL"
    assert detections[0][0]["text"] == "max@test.de"
    assert detections[1] == []


def test_detect_mirrors_the_singular_form(direct_client):
    response = direct_client.post("/privaparse/detect", json={"text": "max@test.de"})
    assert response.status_code == 200
    body = response.json()
    # A caller who sent one text gets one list back, not a list of one list.
    assert body["detections"][0]["type"] == "EMAIL"


def test_detect_answers_one_text_and_several_off_one_assembly(direct_client):
    """Issue #9: the gateway must not answer the same document two ways.

    The gateway owns a caching detector and injects it, so the assembly a
    request comes off is observable from outside — the detection cache counts
    what it served. A singular request that went through some *other* detector
    would leave nothing behind for the plural one to hit, so the hit here is
    what says both forms run through the one injected detector.
    """
    text = "Schreiben Sie an max@test.de"

    singular = direct_client.post("/privaparse/detect", json={"text": text})
    plural = direct_client.post("/privaparse/detect", json={"texts": [text, "nichts hier"]})

    assert singular.json()["detections"] == plural.json()["detections"][0]

    cache = direct_client.get("/privaparse/stats").json()["cache"]
    # One lookup per distinct text: the singular request missed and cached it,
    # the plural request hit that entry and missed only on its second text.
    assert (cache["hits"], cache["misses"]) == (1, 2)


def test_detect_rejects_a_body_that_is_not_an_object(direct_client):
    response = direct_client.post("/privaparse/detect", json=["max@test.de"])
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_detect_rejects_a_missing_text_field(direct_client):
    response = direct_client.post("/privaparse/detect", json={})
    assert response.status_code == 400


def test_detect_preserves_order_and_arity(direct_client):
    texts = ["a@b.de", "nichts", "c@d.de", "", "e@f.de"]
    response = direct_client.post("/privaparse/detect", json={"texts": texts})
    detections = response.json()["detections"]
    # The remote detector in the next piece of work aligns these by index.
    assert len(detections) == len(texts)
    assert [bool(d) for d in detections] == [True, False, True, False, True]


def test_pseudonymize_replaces_and_returns_a_mapping_id(direct_client):
    response = direct_client.post(
        "/privaparse/pseudonymize", json={"text": "Schreiben Sie an max@test.de"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mapping_id"]
    assert "max@test.de" not in body["texts"]
    assert "[[EMAIL_" in body["texts"]


def test_pseudonymize_does_not_leak_personal_data_by_default(direct_client):
    response = direct_client.post(
        "/privaparse/pseudonymize", json={"text": "Schreiben Sie an max@test.de"}
    )
    body = response.json()
    # The promise of the default response: it can be logged safely.
    assert "spans" not in body
    assert "max@test.de" not in response.text


def test_pseudonymize_returns_spans_when_asked(direct_client):
    response = direct_client.post(
        "/privaparse/pseudonymize",
        json={"text": "Schreiben Sie an max@test.de", "include_spans": True},
    )
    spans = response.json()["spans"]
    assert spans[0]["type"] == "EMAIL"
    assert spans[0]["text"] == "max@test.de"
    assert spans[0]["placeholder"].startswith("[[EMAIL_")


def test_pseudonymize_shares_one_mapping_across_texts(direct_client):
    response = direct_client.post(
        "/privaparse/pseudonymize", json={"texts": ["max@test.de", "max@test.de"]}
    )
    body = response.json()
    assert len(body["texts"]) == 2
    # The same value must get the same placeholder, which is the whole point.
    assert body["texts"][0] == body["texts"][1]


def test_pseudonymize_rejects_a_bad_body(direct_client):
    response = direct_client.post("/privaparse/pseudonymize", json={})
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "provide either `text` or `texts`"
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_reverse_round_trips(direct_client):
    original = "Schreiben Sie an max@test.de"
    forward = direct_client.post("/privaparse/pseudonymize", json={"text": original}).json()

    back = direct_client.post(
        "/privaparse/reverse",
        json={"text": forward["texts"], "mapping_id": forward["mapping_id"]},
    )
    assert back.status_code == 200
    assert back.json()["text"] == original
    assert back.json()["restored"] == 1


def test_reverse_finds_the_mapping_without_being_told(direct_client):
    forward = direct_client.post(
        "/privaparse/pseudonymize", json={"text": "max@test.de"}
    ).json()

    back = direct_client.post("/privaparse/reverse", json={"text": forward["texts"]})
    assert back.status_code == 200
    assert back.json()["mapping_id"] == forward["mapping_id"]


def test_reverse_fails_closed_across_two_mappings(direct_client):
    one = direct_client.post("/privaparse/pseudonymize", json={"text": "max@test.de"}).json()
    two = direct_client.post("/privaparse/pseudonymize", json={"text": "eva@test.de"}).json()

    mixed = f"{one['texts']} und {two['texts']}"
    back = direct_client.post("/privaparse/reverse", json={"text": mixed})
    # Partial coverage matches nothing. This is what stops one caller reading
    # another's values, so it is asserted here rather than trusted upstream.
    assert back.status_code == 404
    assert back.json()["error"]["type"] == "mapping_not_found"


def test_reverse_rejects_an_unknown_mapping_id(direct_client):
    back = direct_client.post(
        "/privaparse/reverse", json={"text": "[[EMAIL_A1]]", "mapping_id": "nope"}
    )
    assert back.status_code == 404
    error = back.json()["error"]
    assert error["type"] == "mapping_not_found"
    # UnknownMappingError is a KeyError; str(KeyError(x)) is repr(x), which
    # would wrap this whole message in an extra pair of quotes if the route
    # forwarded it unchanged. The id itself is quoted by `!r` inside the
    # message, and that is the only quoting that should survive.
    assert error["message"] == (
        "no mapping with id 'nope'. "
        "List the mappings this vault knows with: privaparse vault mappings"
    )


def test_reverse_requires_a_text(direct_client):
    assert direct_client.post("/privaparse/reverse", json={}).status_code == 400


def test_catalogue_lists_enabled_types(direct_client):
    body = direct_client.get("/privaparse/catalogue").json()
    names = [t["name"] for t in body["types"]]
    assert "EMAIL" in names
    assert all(t["enabled"] for t in body["types"])
    email = next(t for t in body["types"] if t["name"] == "EMAIL")
    assert "labels" in email and "threshold" in email


def test_vault_reports_counts_and_no_values(direct_client):
    direct_client.post("/privaparse/pseudonymize", json={"text": "max@test.de"})
    response = direct_client.get("/privaparse/vault")
    body = response.json()
    assert body["mappings"] >= 1
    assert body["entities"] >= 1
    assert "by_type" in body
    # Counts only. A stored value appearing here would defeat the point.
    assert "max@test.de" not in response.text


def test_gateway_stats_still_means_gateway_stats(direct_client):
    # /privaparse/stats predates this work and must keep its meaning.
    body = direct_client.get("/privaparse/stats").json()
    assert "mappings" not in body


# --- detect and pseudonymize must agree ------------------------------------


def test_detect_and_pseudonymize_agree_on_a_code_fenced_document(direct_client):
    """/detect must never report PII that /pseudonymize will not remove.

    Before the fix, /detect ran the bare detector over the raw text -- no
    code-fence masking, no threshold, no merge -- so it reported the email
    inside the fenced code block too, while /pseudonymize (which runs the
    full pipeline) left that one in plaintext. A caller using /detect as a
    pre-flight gate would have shipped an unscrubbed document.
    """
    text = 'Kontakt: real@test.de\n\n```python\nsender = "incode@test.de"\n```'

    detected = direct_client.post("/privaparse/detect", json={"text": text})
    pseudonymized = direct_client.post("/privaparse/pseudonymize", json={"text": text})

    assert detected.status_code == 200
    assert pseudonymized.status_code == 200

    detected_texts = {span["text"] for span in detected.json()["detections"]}
    # The code-fenced email is masked by the same pipeline pseudonymize uses,
    # so detect must not report it either.
    assert detected_texts == {"real@test.de"}

    pseudo_text = pseudonymized.json()["texts"]
    assert "real@test.de" not in pseudo_text
    # incode@test.de is inside the fence and is never touched -- that's
    # correct behaviour for pseudonymize. The point of this test is that
    # detect's report and pseudonymize's actual behaviour now agree about it.
    assert "incode@test.de" in pseudo_text


def test_detect_and_pseudonymize_share_the_fixed_key_convention(direct_client):
    """Neither route's response key depends on whether the caller sent
    `text` or `texts` -- only the value's shape does. `pseudonymize` used to
    switch between a `text` key and a `texts` key; now it follows `detect`'s
    rule, so the same singular input collapses to a single item under a
    fixed key on both routes, and the same plural input stays an array under
    that same key on both.
    """
    singular = {"text": "max@test.de"}
    plural = {"texts": ["max@test.de", "eva@test.de"]}

    detect_one = direct_client.post("/privaparse/detect", json=singular).json()
    detect_many = direct_client.post("/privaparse/detect", json=plural).json()
    pseudo_one = direct_client.post("/privaparse/pseudonymize", json=singular).json()
    pseudo_many = direct_client.post("/privaparse/pseudonymize", json=plural).json()

    # The key itself never changes name with arity -- that is what used to
    # differ between the two routes.
    assert "detections" in detect_one
    assert "detections" in detect_many
    assert "texts" in pseudo_one
    assert "texts" in pseudo_many

    # The value collapses with arity instead: a flat list for `text` on
    # detect, a bare string for `text` on pseudonymize; one more level of
    # array for `texts` on both.
    assert isinstance(detect_one["detections"], list)
    assert detect_one["detections"] and isinstance(detect_one["detections"][0], dict)
    assert isinstance(detect_many["detections"], list)
    assert isinstance(detect_many["detections"][0], list)

    assert isinstance(pseudo_one["texts"], str)
    assert isinstance(pseudo_many["texts"], list)
    assert len(pseudo_many["texts"]) == 2


# --- bare 500s: a class of three, not the one that was known ---------------


def test_detect_refuses_with_the_install_guidance_when_gliner2_is_missing(
    settings, upstream, monkeypatch
):
    """First contact on a partial install: `pipx install "privaparse[gateway]"`
    then a call to /privaparse/detect must not come back as an unexplained,
    unshaped 500 -- it has to carry the install guidance, the same as the
    proxy already does for this exact failure (see test_server.py).
    """
    from privaparse.engine import PrivaParseEngine

    monkeypatch.setitem(sys.modules, "gliner2", None)
    hybrid = settings.model_copy(update={"detector": "hybrid"})
    engine = PrivaParseEngine(hybrid, configure_logs=False)
    client = TestClient(create_app(hybrid, engine=engine, upstream=upstream))

    response = client.post("/privaparse/detect", json={"text": "Hallo Max Mustermann"})

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["type"] == "privaparse_model_unavailable"
    assert "pip install -e '.[model]'" in error["message"]
    assert "--detector regex" in error["message"]


def test_pseudonymize_refuses_with_the_install_guidance_when_gliner2_is_missing(
    settings, upstream, monkeypatch
):
    from privaparse.engine import PrivaParseEngine

    monkeypatch.setitem(sys.modules, "gliner2", None)
    hybrid = settings.model_copy(update={"detector": "hybrid"})
    engine = PrivaParseEngine(hybrid, configure_logs=False)
    client = TestClient(create_app(hybrid, engine=engine, upstream=upstream))

    response = client.post(
        "/privaparse/pseudonymize", json={"text": "Hallo Max Mustermann"}
    )

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["type"] == "privaparse_model_unavailable"
    assert "pip install -e '.[model]'" in error["message"]


def test_pseudonymize_rejects_text_that_already_contains_a_placeholder(direct_client):
    """Common when piping `reverse` output back in, or replaying a document
    that was never restored. Must be a shaped 400, not a bare 500.
    """
    response = direct_client.post(
        "/privaparse/pseudonymize", json={"text": "Hello [[EMAIL_A1]]"}
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert "already contains PrivaParse placeholders" in error["message"]


def test_reverse_strict_true_rejects_a_foreign_placeholder_with_400(direct_client):
    """`strict: true` on a placeholder from another mapping used to raise
    ForeignPlaceholderError straight through the route as a bare 500. It
    must come back shaped like every other rejection this route makes.
    """
    one = direct_client.post("/privaparse/pseudonymize", json={"text": "max@test.de"}).json()
    two = direct_client.post("/privaparse/pseudonymize", json={"text": "eva@test.de"}).json()

    response = direct_client.post(
        "/privaparse/reverse",
        json={"text": one["texts"], "mapping_id": two["mapping_id"], "strict": True},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert "another mapping" in error["message"]


# --- mapping_id: "" must be treated as absent -------------------------------


def test_reverse_treats_an_empty_mapping_id_as_absent(direct_client):
    forward = direct_client.post(
        "/privaparse/pseudonymize", json={"text": "max@test.de"}
    ).json()

    response = direct_client.post(
        "/privaparse/reverse", json={"text": forward["texts"], "mapping_id": ""}
    )
    assert response.status_code == 200
    body = response.json()
    # The route's own discovery must have run, and reported the mapping it
    # actually found -- not the empty string the caller cannot use again.
    assert body["mapping_id"] == forward["mapping_id"]
    assert body["mapping_id"] != ""


# --- /reverse must share the sibling routes' body validation ---------------


def test_reverse_rejects_a_body_that_is_not_an_object(direct_client):
    response = direct_client.post("/privaparse/reverse", json=["max@test.de"])
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    # Must agree with detect/pseudonymize's message for the same mistake,
    # not fall through to "`text` must be a string" because a list has no
    # `.get`.
    assert error["message"] == "the request body must be a JSON object"


# --- include_spans and strict must both be validated as booleans -----------


def test_reverse_rejects_a_non_boolean_strict(direct_client):
    response = direct_client.post(
        "/privaparse/reverse",
        json={"text": "[[EMAIL_A1]]", "mapping_id": "nope", "strict": "false"},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["message"] == "`strict` must be a boolean"


def test_reverse_accepts_strict_as_an_explicit_false(direct_client):
    forward = direct_client.post(
        "/privaparse/pseudonymize", json={"text": "max@test.de"}
    ).json()

    response = direct_client.post(
        "/privaparse/reverse",
        json={
            "text": forward["texts"],
            "mapping_id": forward["mapping_id"],
            "strict": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["restored"] == 1


def test_pseudonymize_rejects_a_non_boolean_include_spans(direct_client):
    response = direct_client.post(
        "/privaparse/pseudonymize",
        json={"text": "max@test.de", "include_spans": "true"},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["message"] == "`include_spans` must be a boolean"


def test_pseudonymize_accepts_include_spans_as_an_explicit_false(direct_client):
    response = direct_client.post(
        "/privaparse/pseudonymize",
        json={"text": "max@test.de", "include_spans": False},
    )
    assert response.status_code == 200
    assert "spans" not in response.json()


# --- every rejection branch, and the documented-but-untested fields --------


def test_detect_rejects_a_non_string_texts_item(direct_client):
    response = direct_client.post("/privaparse/detect", json={"texts": ["ok", 5]})
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "`texts` must be an array of strings"


def test_detect_rejects_a_non_string_text(direct_client):
    response = direct_client.post("/privaparse/detect", json={"text": 5})
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "`text` must be a string"


def test_detect_rejects_invalid_json(direct_client):
    response = direct_client.post(
        "/privaparse/detect",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "the request body must be valid JSON"


def test_pseudonymize_rejects_a_non_string_source_name(direct_client):
    response = direct_client.post(
        "/privaparse/pseudonymize", json={"text": "max@test.de", "source_name": 5}
    )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "`source_name` must be a string"


def test_reverse_rejects_a_non_string_text(direct_client):
    response = direct_client.post("/privaparse/reverse", json={"text": 5})
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "`text` must be a string"


def test_reverse_rejects_a_non_string_mapping_id(direct_client):
    response = direct_client.post(
        "/privaparse/reverse", json={"text": "[[EMAIL_A1]]", "mapping_id": 5}
    )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "`mapping_id` must be a string"


def test_reverse_reports_foreign_placeholders_without_strict(direct_client):
    one = direct_client.post("/privaparse/pseudonymize", json={"text": "max@test.de"}).json()
    two = direct_client.post("/privaparse/pseudonymize", json={"text": "eva@test.de"}).json()

    response = direct_client.post(
        "/privaparse/reverse",
        json={"text": one["texts"], "mapping_id": two["mapping_id"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["foreign"] == [one["texts"]]
    assert body["restored"] == 0
    # Nothing leaked: the foreign placeholder is left standing, not restored.
    assert body["text"] == one["texts"]


def test_reverse_reports_unknown_placeholders(direct_client):
    forward = direct_client.post(
        "/privaparse/pseudonymize", json={"text": "max@test.de"}
    ).json()

    response = direct_client.post(
        "/privaparse/reverse",
        json={"text": "[[EMAIL_Z9]]", "mapping_id": forward["mapping_id"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["unknown"] == ["[[EMAIL_Z9]]"]
    assert body["restored"] == 0


# --- every handler must offload its blocking work off the event loop -------


def _spy_on_run_in_threadpool(monkeypatch):
    """Wrap direct.py's `run_in_threadpool` so a test can prove a handler
    routed its blocking call through it, without measuring wall-clock time --
    a timing assertion would be fragile on a slow CI runner, while this is
    exact regardless of how fast the machine is.
    """
    import privaparse.gateway.direct as direct_module

    original = direct_module.run_in_threadpool
    calls: list = []

    async def spy(func, *args, **kwargs):
        calls.append(func)
        return await original(func, *args, **kwargs)

    monkeypatch.setattr(direct_module, "run_in_threadpool", spy)
    return calls


def test_detect_offloads_detection_to_a_worker_thread(direct_client, monkeypatch):
    calls = _spy_on_run_in_threadpool(monkeypatch)
    response = direct_client.post("/privaparse/detect", json={"text": "max@test.de"})
    assert response.status_code == 200
    assert calls, "detect must run detection through run_in_threadpool"


def test_pseudonymize_offloads_its_work_to_a_worker_thread(direct_client, monkeypatch):
    calls = _spy_on_run_in_threadpool(monkeypatch)
    response = direct_client.post(
        "/privaparse/pseudonymize", json={"text": "max@test.de"}
    )
    assert response.status_code == 200
    assert calls, "pseudonymize must run pseudonymize_batch through run_in_threadpool"


def test_reverse_offloads_its_work_to_a_worker_thread(direct_client, monkeypatch):
    forward = direct_client.post(
        "/privaparse/pseudonymize", json={"text": "max@test.de"}
    ).json()

    calls = _spy_on_run_in_threadpool(monkeypatch)
    response = direct_client.post(
        "/privaparse/reverse",
        json={"text": forward["texts"], "mapping_id": forward["mapping_id"]},
    )
    assert response.status_code == 200
    assert calls, "reverse must run its vault lookups through run_in_threadpool"


def test_vault_offloads_its_stats_lookup_to_a_worker_thread(direct_client, monkeypatch):
    calls = _spy_on_run_in_threadpool(monkeypatch)
    response = direct_client.get("/privaparse/vault")
    assert response.status_code == 200
    assert calls, "vault must run vault_stats through run_in_threadpool"
