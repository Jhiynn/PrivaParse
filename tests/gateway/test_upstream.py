from __future__ import annotations

from privaparse.gateway.upstream import Upstream


def test_only_the_allow_listed_headers_go_upstream():
    """`_headers` is the entire boundary between what a client sent and what
    leaves the machine. Every other test in this package drives FakeUpstream,
    which has no filtering of its own -- it records whatever it is handed --
    so none of them would notice if this stopped filtering. Pure staticmethod:
    no client, no network, no async."""
    kept = Upstream._headers(
        {
            "authorization": "Bearer sk-test",
            "content-type": "application/json",
            "cookie": "session=abc",
            "x-forwarded-for": "10.0.0.1",
        }
    )
    assert kept == {"authorization": "Bearer sk-test", "content-type": "application/json"}


def test_every_allow_listed_header_survives():
    """The other direction: nothing named in `_FORWARDED` gets dropped.

    Without this, the test above would still pass if `_FORWARDED` shrank to
    just `("authorization",)` -- it never exercises `openai-organization` or
    `openai-project` at all.
    """
    incoming = {
        "authorization": "Bearer sk-test",
        "content-type": "application/json",
        "openai-organization": "org-1",
        "openai-project": "proj-1",
    }
    assert Upstream._headers(incoming) == incoming
