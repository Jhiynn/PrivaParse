"""The direct API: PrivaParse's own capabilities over HTTP, not a proxy."""

from __future__ import annotations


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
    assert "max@test.de" not in body["text"]
    assert "[[EMAIL_" in body["text"]


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
    assert direct_client.post("/privaparse/pseudonymize", json={}).status_code == 400
