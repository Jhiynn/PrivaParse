"""A stand-in for the LLM, so the round trip can be exercised end to end.

Phase 1 calls no external service. What matters for the test is only that the
"response" carries placeholders forward the way a real model would — including
reordering them and mentioning some of them more than once.
"""

from __future__ import annotations

from privaparse.database.placeholder import find_placeholders

__all__ = ["mock_llm_response"]

_GREETING = "Sehr geehrte(r) {person},"
_FALLBACK_GREETING = "Guten Tag,"


def mock_llm_response(text: str) -> str:
    """Produce a plausible German reply that reuses the placeholders in ``text``."""
    by_type: dict[str, list[str]] = {}
    for match in find_placeholders(text):
        by_type.setdefault(match.group(1), []).append(match.group(0))

    persons = _unique(by_type.get("PERSON", []))
    emails = _unique(by_type.get("EMAIL", []))
    phones = _unique(by_type.get("PHONE", []))

    lines: list[str] = []
    lines.append(_GREETING.format(person=persons[0]) if persons else _FALLBACK_GREETING)
    lines.append("")
    lines.append("vielen Dank für Ihre Nachricht. Wir haben Ihre Angaben aufgenommen.")

    contact = _contact_sentence(emails, phones)
    if contact:
        lines.append(contact)

    if len(persons) > 1:
        others = ", ".join(persons[1:])
        lines.append(f"Eine Kopie geht an {others}.")

    if persons:
        # Repeat the first placeholder on purpose: reversal has to handle a
        # placeholder appearing more than once in the response.
        lines.append(f"Bei Rückfragen wenden Sie sich bitte an {persons[0]}.")

    lines.extend(["", "Mit freundlichen Grüßen", "Musterfirma GmbH"])
    return "\n".join(lines) + "\n"


def _contact_sentence(emails: list[str], phones: list[str]) -> str:
    if emails and phones:
        return f"Sie erreichen uns unter {emails[0]} oder telefonisch unter {phones[0]}."
    if emails:
        return f"Sie erreichen uns unter {emails[0]}."
    if phones:
        return f"Sie erreichen uns telefonisch unter {phones[0]}."
    return ""


def _unique(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)
