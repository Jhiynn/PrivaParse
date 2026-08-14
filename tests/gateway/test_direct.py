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
