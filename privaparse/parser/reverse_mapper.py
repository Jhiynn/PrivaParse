"""Put the original values back.

The rule that shapes this module: **a placeholder is only resolved for the
document that was issued it.** The vault is global, so without that scope
anyone who can call ``reverse()`` could write ``[[PERSON_A47]]`` into a document
and read back the name of a person they have never seen. Placeholders from other
sessions are left in place and reported, not resolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from privaparse.app.logging import get_logger
from privaparse.database.placeholder import PLACEHOLDER_RE
from privaparse.database.repository import VaultRepository

log = get_logger("reverse")

__all__ = [
    "ForeignPlaceholderError",
    "NoCoveringMappingError",
    "ReverseResult",
    "UnknownMappingError",
    "find_mapping_for",
    "reverse_text",
]


class UnknownMappingError(KeyError):
    """Raised when the given mapping id does not exist."""


class ForeignPlaceholderError(RuntimeError):
    """Raised in strict mode when a placeholder belongs to another session."""


class NoCoveringMappingError(LookupError):
    """Raised when no single session issued every placeholder in the text."""


def find_mapping_for(text: str, *, repo: VaultRepository) -> str:
    """Identify the session that issued every placeholder in ``text``.

    Convenience, not a loophole. Full coverage is required, so a document
    carrying a placeholder from elsewhere matches nothing and the caller is
    forced to name a mapping — where the foreign placeholder is then refused
    by the ordinary rule.
    """
    wanted = {match.group(0) for match in PLACEHOLDER_RE.finditer(text)}
    if not wanted:
        raise NoCoveringMappingError("this text contains no placeholders to restore")

    covering = repo.find_covering_mappings(wanted)
    if not covering:
        known = [p for p in sorted(wanted) if repo.placeholder_is_known(p)]
        unknown = sorted(wanted - set(known))
        detail = (
            f" {len(unknown)} placeholder(s) were never issued by this vault: "
            f"{', '.join(unknown[:5])}"
            if unknown
            else " the placeholders are spread across several sessions"
        )
        raise NoCoveringMappingError(
            f"no single session issued all {len(wanted)} placeholder(s) in this text —"
            f"{detail}. List sessions with: privaparse vault mappings"
        )

    chosen = covering[0]
    log.info(
        "selected mapping %s (%s, %d placeholders) — covers all %d in this text",
        chosen.id,
        chosen.created_at.strftime("%Y-%m-%d %H:%M"),
        chosen.placeholders,
        len(wanted),
    )
    return chosen.id


@dataclass(frozen=True)
class ReverseResult:
    text: str
    restored: int = 0
    #: Placeholders the vault knows, but which this mapping was never issued.
    foreign: list[str] = field(default_factory=list)
    #: Placeholders the vault has never issued to anyone — invented downstream.
    unknown: list[str] = field(default_factory=list)
    #: Restored by the tolerant matcher rather than exactly. Counted apart so
    #: an operator can see how often the model is mangling placeholders, which
    #: is a property of the model and worth knowing.
    recovered: int = 0

    @property
    def is_clean(self) -> bool:
        return not self.foreign and not self.unknown


def _tolerant_pattern(placeholder: str) -> re.Pattern[str] | None:
    """A pattern for the ways a model tends to rewrite one placeholder.

    Measured manglings, all from one afternoon against a 1.5B model: a bracket
    pair dropped, brackets unbalanced, quotes injected inside them, padding
    added, brackets gone entirely, and the underscore turned into a space with
    the capitals lowered ("Person A1").

    What is *not* loosened is which placeholders may match at all. The pattern
    is built from one entry of one mapping's restore table, so widening the
    spelling cannot widen the reach: a placeholder this session never issued
    has no pattern here and matches nothing however it is written.

    The type name and the suffix must both appear. Either alone is a word
    ("Person") or a fragment; together they are distinctive enough that a
    false match would have to be deliberate.
    """
    parsed = PLACEHOLDER_RE.fullmatch(placeholder)
    if parsed is None:  # pragma: no cover - the vault only stores rendered ones
        return None

    type_name, suffix = parsed.group(1), parsed.group(2)
    # A type name may itself contain underscores (NATIONAL_ID), and a model
    # that spaces one out will space out the others too.
    body = r"[_\s]*".join(re.escape(part) for part in type_name.split("_") if part)
    body = rf"{body}[_\s]*{re.escape(suffix)}"
    return re.compile(
        rf"(?<!\w)\[{{0,2}}\s*[\"']?\s*{body}\s*[\"']?\s*\]{{0,2}}(?!\w)",
        re.IGNORECASE,
    )


def reverse_text(
    mapping_id: str,
    text: str,
    *,
    repo: VaultRepository,
    strict: bool = False,
    fuzzy: bool = False,
) -> ReverseResult:
    """Restore the placeholders this mapping issued, and only those.

    ``fuzzy`` adds a second pass that also accepts the ways a model rewrites a
    placeholder — see :func:`_tolerant_pattern`. Off by default: exact matching
    is the behaviour every other caller of this function has always had, and
    loosening it is a decision for whoever runs the gateway.
    """
    if repo.get_mapping(mapping_id) is None:
        raise UnknownMappingError(
            f"no mapping with id {mapping_id!r}. "
            f"List the sessions this vault knows with: privaparse vault mappings"
        )

    table = repo.restore_table(mapping_id)

    restored = 0
    foreign: list[str] = []
    unknown: list[str] = []

    def _substitute(match) -> str:  # type: ignore[no-untyped-def]
        nonlocal restored
        placeholder = match.group(0)
        if placeholder in table:
            restored += 1
            return table[placeholder]

        if repo.placeholder_is_known(placeholder):
            if placeholder not in foreign:
                foreign.append(placeholder)
        elif placeholder not in unknown:
            unknown.append(placeholder)
        return placeholder

    new_text = PLACEHOLDER_RE.sub(_substitute, text)

    recovered = 0
    if fuzzy:
        for placeholder, value in table.items():
            pattern = _tolerant_pattern(placeholder)
            if pattern is None:  # pragma: no cover - defensive
                continue
            # A callable replacement, so a stored value containing a backslash
            # or a group reference goes in literally rather than being read as
            # one -- names do contain both.
            new_text, hits = pattern.subn(lambda _match, v=value: v, new_text)
            recovered += hits

    if foreign:
        log.warning(
            "%d placeholder(s) belong to a different mapping and were left in place: %s",
            len(foreign),
            ", ".join(foreign),
        )
        if strict:
            raise ForeignPlaceholderError(
                f"placeholders from another session appeared in this text: "
                f"{', '.join(foreign)}"
            )
    if unknown:
        log.warning(
            "%d placeholder(s) were never issued by this vault and were left in "
            "place (invented downstream?): %s",
            len(unknown),
            ", ".join(unknown),
        )

    if recovered:
        log.info(
            "%d placeholder(s) were restored only after tolerating how the model "
            "rewrote them",
            recovered,
        )
    log.info("restored %d placeholder(s) for mapping %s", restored + recovered, mapping_id)
    return ReverseResult(
        text=new_text,
        restored=restored,
        foreign=foreign,
        unknown=unknown,
        recovered=recovered,
    )
