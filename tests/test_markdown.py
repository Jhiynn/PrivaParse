"""Markdown masking. The invariant that everything else relies on: the masked
view has exactly the same length as the original, so offsets are interchangeable."""

from __future__ import annotations

from privaparse.parser.markdown import protect, protected_regions

FENCED = """\
Hallo Max Mustermann,

```python
user = "max@test.de"
phone = "+49 170 1234567"
```

Bis bald.
"""


def _assert_offsets_are_interchangeable(text: str) -> None:
    p = protect(text)
    assert len(p.view) == len(p.original)
    # Newlines must survive so the model still sees paragraph structure.
    assert p.view.count("\n") == p.original.count("\n")


def test_masking_is_length_preserving() -> None:
    _assert_offsets_are_interchangeable(FENCED)


def test_fenced_code_is_masked_but_prose_survives() -> None:
    p = protect(FENCED)
    assert "Max Mustermann" in p.view
    assert "max@test.de" not in p.view
    assert "+49 170 1234567" not in p.view


def test_original_is_never_modified() -> None:
    p = protect(FENCED)
    assert p.original == FENCED


def test_inline_code_is_masked() -> None:
    text = "Schreib an `max@test.de` statt an erika@test.de."
    p = protect(text)
    assert "max@test.de" not in p.view
    assert "erika@test.de" in p.view


def test_tilde_fences_are_recognised() -> None:
    text = "vor\n~~~\nmax@test.de\n~~~\nnach"
    p = protect(text)
    assert "max@test.de" not in p.view
    assert "vor" in p.view and "nach" in p.view


def test_unterminated_fence_protects_to_end_of_document() -> None:
    text = "Hallo\n```\nmax@test.de\nnoch mehr code"
    p = protect(text)
    assert "Hallo" in p.view
    assert "max@test.de" not in p.view


def test_urls_are_masked() -> None:
    text = "Siehe https://mueller-partner.de/team und www.beispiel.de."
    p = protect(text)
    assert "mueller-partner.de" not in p.view
    assert "beispiel.de" not in p.view


def test_mailto_links_are_deliberately_left_scannable() -> None:
    """A mailto target *is* the PII we are looking for."""
    text = "Mail: <mailto:max@test.de> und [Mail](mailto:erika@test.de)"
    p = protect(text)
    assert "max@test.de" in p.view
    assert "erika@test.de" in p.view


def test_link_destination_is_masked_but_link_text_is_not() -> None:
    text = "[Max Mustermann](https://firma.de/profil)"
    p = protect(text)
    assert "Max Mustermann" in p.view
    assert "firma.de" not in p.view


def test_yaml_frontmatter_is_scanned() -> None:
    """Frontmatter routinely carries author names — protecting it would hide them."""
    text = "---\nauthor: Max Mustermann\n---\n\nText."
    p = protect(text)
    assert "Max Mustermann" in p.view


def test_html_comments_are_masked() -> None:
    text = "Sichtbar. <!-- intern: max@test.de --> Ende."
    p = protect(text)
    assert "max@test.de" not in p.view
    assert "Sichtbar." in p.view


def test_indented_blocks_stay_scannable() -> None:
    """Four-space indentation is ambiguous with lists; hiding a real name there
    would be the more expensive mistake."""
    text = "Notiz:\n\n    Max Mustermann war da.\n"
    p = protect(text)
    assert "Max Mustermann" in p.view


def test_scan_code_disables_all_masking() -> None:
    p = protect(FENCED, scan_code=True)
    assert p.view == p.original
    assert p.regions == ()


def test_is_protected_reports_region_membership() -> None:
    p = protect(FENCED)
    index = FENCED.index("max@test.de")
    assert p.is_protected(index, index + 5) is True

    prose = FENCED.index("Max Mustermann")
    assert p.is_protected(prose, prose + 5) is False


def test_regions_are_sorted_and_disjoint() -> None:
    text = "`a` und `b` und ```\ncode\n``` und https://x.de"
    regions = protected_regions(text)
    assert regions == sorted(regions, key=lambda r: r.start)
    for left, right in zip(regions, regions[1:]):
        assert left.end <= right.start


def test_empty_document_is_handled() -> None:
    p = protect("")
    assert p.view == ""
    assert p.regions == ()
