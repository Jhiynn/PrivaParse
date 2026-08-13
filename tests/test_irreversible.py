from __future__ import annotations

import pytest

from privaparse.app.catalogue import load_catalogue
from privaparse.database.models import Entity, EntityValue
from privaparse.parser.entity_resolver import EntityResolver, UnknownEntityTypeError
from privaparse.parser.types import SOURCE_GLINER, Span

SECRET = "sk-live-4f3a9c2b8e1d"  # noqa: S105 -- this project's entity-type name, not a real credential

CATALOGUE = """
version: 1
placeholder_types:
  SECRET:
    labels: [secret]
    normalizer: identity
    sweep: exact
    reversible: false
"""


@pytest.fixture
def catalogue(tmp_path):
    target = tmp_path / "privaparse.entities.yaml"
    target.write_text(CATALOGUE, encoding="utf-8")
    return load_catalogue(target)


def test_secret_value_never_reaches_the_database(repo, catalogue):
    span = Span(0, len(SECRET), SECRET, "SECRET", 0.9, SOURCE_GLINER)
    EntityResolver(repo, catalogue).resolve([span])
    repo.session.commit()

    for entity in repo.session.query(Entity).all():
        assert SECRET not in repo.normalized_value_of(entity)
    assert repo.session.query(EntityValue).count() == 0


def test_secret_still_gets_a_stable_placeholder(repo, catalogue):
    resolver = EntityResolver(repo, catalogue)
    first = resolver.resolve([Span(0, len(SECRET), SECRET, "SECRET", 0.9, SOURCE_GLINER)])
    second = resolver.resolve([Span(0, len(SECRET), SECRET, "SECRET", 0.9, SOURCE_GLINER)])
    assert first.spans[0].placeholder == second.spans[0].placeholder


def test_irreversible_entity_records_no_usage(repo, catalogue):
    resolution = EntityResolver(repo, catalogue).resolve(
        [Span(0, len(SECRET), SECRET, "SECRET", 0.9, SOURCE_GLINER)]
    )
    assert resolution.spans
    assert resolution.usages == {}


def test_reversible_type_is_unaffected(repo):
    resolver = EntityResolver(repo, load_catalogue())
    resolution = resolver.resolve([Span(0, 14, "Max Mustermann", "PERSON", 0.9, SOURCE_GLINER)])
    assert len(resolution.usages) == 1


def test_unknown_type_fails_before_anything_is_written(repo):
    resolver = EntityResolver(repo, load_catalogue())
    with pytest.raises(UnknownEntityTypeError, match="NOPE"):
        resolver.resolve([Span(0, 3, "abc", "NOPE", 0.9, SOURCE_GLINER)])
    assert repo.session.query(Entity).count() == 0


def test_a_valid_span_before_an_unknown_type_still_writes_nothing(repo):
    """The single-span version above passes trivially: there is nothing before
    the bad span for a write to happen to. A valid PERSON ahead of the bad
    span is the case that actually exercises "before anything is written" —
    the type check has to run for every span before any span's write, not
    just check-then-write one at a time in document order.
    """
    resolver = EntityResolver(repo, load_catalogue())
    spans = [
        Span(0, 14, "Max Mustermann", "PERSON", 0.9, SOURCE_GLINER),
        Span(20, 23, "abc", "NOPE", 0.9, SOURCE_GLINER),
    ]
    with pytest.raises(UnknownEntityTypeError, match="NOPE"):
        resolver.resolve(spans)
    assert repo.session.query(Entity).count() == 0
