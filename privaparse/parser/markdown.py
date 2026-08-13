"""Markdown-aware masking of regions that should not be scanned for PII.

The trick that makes this cheap and safe: masking is **length-preserving**.
Every protected character is replaced by a space (newlines survive, so the model
still sees paragraph structure), which means an offset in the masked view is the
same offset in the original document. No remapping, and therefore no
off-by-one bugs at the seams.

Two deliberate choices:

*Indented code blocks are not protected.* Four-space indentation is ambiguous
with list continuation and quoted text in real documents. Protecting it risks
hiding a real name inside an indented block, and a missed name leaves the
machine. A false positive only costs readability, so the asymmetry decides it.

*``mailto:`` links are not protected*, even though other URLs are. A
``mailto:`` target is an actual email address — protecting it would hide exactly
the thing we are looking for.

Known limitation: a person's name inside a URL path
(``https://firma.de/team/max-mustermann``) is not detected while URLs are
protected. Run with ``scan_code=True`` if that matters more than false positives
on domain names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["ProtectedText", "Region", "protect", "protected_regions"]

_FENCE_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)
_AUTOLINK_RE = re.compile(r"<([a-zA-Z][a-zA-Z0-9+.-]*:[^<>\s]+)>")
_BARE_URL_RE = re.compile(r"(?<![\w@])(?:https?://|www\.)[^\s<>\)\]\"']+")
_LINK_DESTINATION_RE = re.compile(r"\]\(\s*(<[^>]*>|[^\s\)]+)")
_REFERENCE_DEF_RE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*(\S+)", re.MULTILINE)

#: Never protect a URL that is itself an email address.
_MAILTO_PREFIX = "mailto:"


@dataclass(frozen=True, slots=True)
class Region:
    """A half-open character range that must not be scanned."""

    start: int
    end: int
    kind: str

    def contains(self, index: int) -> bool:
        return self.start <= index < self.end


@dataclass(frozen=True, slots=True)
class ProtectedText:
    """The original document plus a masked view with identical offsets."""

    original: str
    view: str
    regions: tuple[Region, ...] = field(default=())

    def is_protected(self, start: int, end: int) -> bool:
        """True if ``[start, end)`` touches any protected region."""
        return any(start < r.end and r.start < end for r in self.regions)

    def region_at(self, index: int) -> Region | None:
        for region in self.regions:
            if region.contains(index):
                return region
        return None


def protect(text: str, *, scan_code: bool = False) -> ProtectedText:
    """Build the detection view for ``text``.

    With ``scan_code=True`` nothing is masked and the view is the original.
    """
    if scan_code:
        return ProtectedText(original=text, view=text, regions=())

    regions = protected_regions(text)
    return ProtectedText(original=text, view=_mask(text, regions), regions=tuple(regions))


def protected_regions(text: str) -> list[Region]:
    """All regions to exclude from detection, merged and sorted."""
    regions: list[Region] = []
    regions.extend(_fenced_code_regions(text))
    regions.extend(_regex_regions(text, _HTML_COMMENT_RE, "html-comment"))

    # Everything below can legitimately appear inside a fence; skipping regions
    # we already have keeps the list small and the merge cheap.
    taken = _merge(regions)
    regions.extend(_inline_code_regions(text, taken))
    regions.extend(_url_regions(text, taken))

    return _merge(regions)


# --- individual scanners ---------------------------------------------------


def _fenced_code_regions(text: str) -> list[Region]:
    """Fenced blocks (``` or ~~~), including their fence lines."""
    regions: list[Region] = []
    offset = 0
    open_at: int | None = None
    open_char = ""
    open_len = 0

    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        match = _FENCE_RE.match(stripped)
        if match:
            fence = match.group(2)
            char, length = fence[0], len(fence)
            if open_at is None:
                open_at, open_char, open_len = offset, char, length
            elif char == open_char and length >= open_len and not match.group(3).strip():
                regions.append(Region(open_at, offset + len(line), "fenced-code"))
                open_at = None
        offset += len(line)

    if open_at is not None:
        # Unterminated fence: protect to end of document. Treating the rest as
        # prose would be the riskier guess, since it is probably all code.
        regions.append(Region(open_at, len(text), "fenced-code"))
    return regions


def _inline_code_regions(text: str, taken: list[Region]) -> list[Region]:
    return [
        Region(m.start(), m.end(), "inline-code")
        for m in _INLINE_CODE_RE.finditer(text)
        if not _intersects(taken, m.start(), m.end())
    ]


def _url_regions(text: str, taken: list[Region]) -> list[Region]:
    regions: list[Region] = []

    for match in _AUTOLINK_RE.finditer(text):
        if _is_mailto(match.group(1)):
            continue
        if not _intersects(taken, match.start(), match.end()):
            regions.append(Region(match.start(), match.end(), "autolink"))

    for match in _BARE_URL_RE.finditer(text):
        if not _intersects(taken, match.start(), match.end()):
            regions.append(Region(match.start(), match.end(), "url"))

    for pattern, kind in ((_LINK_DESTINATION_RE, "link-target"), (_REFERENCE_DEF_RE, "link-ref")):
        for match in pattern.finditer(text):
            target = match.group(1)
            if _is_mailto(target.lstrip("<")):
                continue
            start, end = match.span(1)
            if not _intersects(taken, start, end):
                regions.append(Region(start, end, kind))

    return regions


def _regex_regions(text: str, pattern: re.Pattern[str], kind: str) -> list[Region]:
    return [Region(m.start(), m.end(), kind) for m in pattern.finditer(text)]


# --- helpers ---------------------------------------------------------------


def _is_mailto(value: str) -> bool:
    return value.lower().startswith(_MAILTO_PREFIX)


def _intersects(regions: list[Region], start: int, end: int) -> bool:
    return any(start < r.end and r.start < end for r in regions)


def _merge(regions: list[Region]) -> list[Region]:
    """Sort and coalesce overlapping regions."""
    if not regions:
        return []
    ordered = sorted(regions, key=lambda r: (r.start, -r.end))
    merged = [ordered[0]]
    for region in ordered[1:]:
        last = merged[-1]
        if region.start <= last.end:
            if region.end > last.end:
                kind = last.kind if last.kind == region.kind else "mixed"
                merged[-1] = Region(last.start, region.end, kind)
        else:
            merged.append(region)
    return merged


def _mask(text: str, regions: list[Region]) -> str:
    """Blank out protected characters while keeping every offset intact."""
    if not regions:
        return text
    chars = list(text)
    for region in regions:
        for i in range(region.start, min(region.end, len(chars))):
            if chars[i] not in "\r\n":
                chars[i] = " "
    return "".join(chars)
