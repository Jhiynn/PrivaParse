"""Vault behaviour — the core promise is placeholder stability across documents."""

from __future__ import annotations

from privaparse.database.repository import Database, VaultRepository


def test_same_value_returns_same_entity(repo: VaultRepository) -> None:
    first = repo.get_or_create_entity("PERSON", "max mustermann")
    second = repo.get_or_create_entity("PERSON", "max mustermann")
    assert first.id == second.id
    assert first.placeholder == second.placeholder


def test_suffix_counter_is_global_across_types(repo: VaultRepository) -> None:
    person = repo.get_or_create_entity("PERSON", "max mustermann")
    email = repo.get_or_create_entity("EMAIL", "max@test.de")
    phone = repo.get_or_create_entity("PHONE", "+491701234567")

    # A per-type counter would give all three the suffix A1, which reads as if
    # they belong to one person. Phase 1 does no linking, so it must not.
    assert person.placeholder == "[[PERSON_A1]]"
    assert email.placeholder == "[[EMAIL_A2]]"
    assert phone.placeholder == "[[PHONE_A3]]"


def test_same_normalized_value_different_type_is_a_different_entity(
    repo: VaultRepository,
) -> None:
    a = repo.get_or_create_entity("PERSON", "collision")
    b = repo.get_or_create_entity("EMAIL", "collision")
    assert a.id != b.id
    assert a.placeholder != b.placeholder


def test_placeholder_is_stable_across_documents(database: Database) -> None:
    """The headline guarantee of a global vault."""
    with database.session() as session:
        repo_a = VaultRepository(session)
        doc_a_person = repo_a.get_or_create_entity("PERSON", "max mustermann")
        repo_a.get_or_create_entity("EMAIL", "max@test.de")
        session.commit()
        placeholder_in_doc_a = doc_a_person.placeholder

    with database.session() as session:
        repo_b = VaultRepository(session)
        # A second, unrelated document that happens to mention the same person.
        repo_b.get_or_create_entity("PHONE", "+491701234567")
        doc_b_person = repo_b.get_or_create_entity("PERSON", "max mustermann")
        session.commit()

    assert doc_b_person.placeholder == placeholder_in_doc_a


def test_surface_forms_are_deduplicated(repo: VaultRepository) -> None:
    entity = repo.get_or_create_entity("PERSON", "max mustermann")
    first = repo.record_surface_form(entity, "Max Mustermann")
    again = repo.record_surface_form(entity, "Max Mustermann")
    variant = repo.record_surface_form(entity, "MAX MUSTERMANN")

    assert first.id == again.id
    assert variant.id != first.id
    assert repo.surface_value_of(first) == "Max Mustermann"


def test_restore_table_is_scoped_to_one_mapping(repo: VaultRepository) -> None:
    person = repo.get_or_create_entity("PERSON", "max mustermann")
    other = repo.get_or_create_entity("PERSON", "erika musterfrau")
    person_value = repo.record_surface_form(person, "Max Mustermann")
    other_value = repo.record_surface_form(other, "Erika Musterfrau")

    mapping_a = repo.create_mapping(text_sha256="a" * 64, source_name="a.md")
    repo.add_mapping_entry(mapping_a, person, person_value)

    mapping_b = repo.create_mapping(text_sha256="b" * 64, source_name="b.md")
    repo.add_mapping_entry(mapping_b, other, other_value)
    repo.session.commit()

    table_a = repo.restore_table(mapping_a.id)
    assert table_a == {"[[PERSON_A1]]": "Max Mustermann"}
    # Document A must not be able to resolve a placeholder it never received.
    assert other.placeholder not in table_a

    table_b = repo.restore_table(mapping_b.id)
    assert table_b == {"[[PERSON_A2]]": "Erika Musterfrau"}


def test_placeholder_is_known_distinguishes_foreign_from_invented(
    repo: VaultRepository,
) -> None:
    entity = repo.get_or_create_entity("PERSON", "max mustermann")
    repo.session.commit()

    assert repo.placeholder_is_known(entity.placeholder) is True
    assert repo.placeholder_is_known("[[PERSON_ZZ9]]") is False


def test_a_suffix_collision_does_not_undo_earlier_entities(
    repo: VaultRepository, monkeypatch
) -> None:
    """A plain rollback on collision would discard every entity already created
    for the same document, silently losing their placeholders."""
    first = repo.get_or_create_entity("PERSON", "max mustermann")
    second = repo.get_or_create_entity("EMAIL", "max@test.de")

    # Force the next allocation to collide once by handing back a used index.
    original = repo._next_suffix_index
    calls = {"n": 0}

    def colliding() -> int:
        calls["n"] += 1
        return 0 if calls["n"] == 1 else original()

    monkeypatch.setattr(repo, "_next_suffix_index", colliding)

    third = repo.get_or_create_entity("PHONE", "+491701234567")

    assert calls["n"] >= 2, "the collision path was not exercised"
    assert third.placeholder == "[[PHONE_A3]]"
    # The two entities created before the collision must still be there.
    assert repo.find_entity("PERSON", "max mustermann").id == first.id
    assert repo.find_entity("EMAIL", "max@test.de").id == second.id


def test_stats_counts_by_type(repo: VaultRepository) -> None:
    repo.get_or_create_entity("PERSON", "a")
    repo.get_or_create_entity("PERSON", "b")
    repo.get_or_create_entity("EMAIL", "c@d.de")
    repo.session.commit()

    stats = repo.stats()
    assert stats.entities == 3
    assert stats.by_type == {"PERSON": 2, "EMAIL": 1}
