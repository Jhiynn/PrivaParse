"""Compile the annotated gold source into JSONL with character offsets.

Hand-counted offsets are wrong offsets. The canonical artefact is
``gold/de_gold_source.md``, where entities are marked inline::

    Sehr geehrter Herr {{PERSON:Dr. Max Mustermann}},

This script strips the markers, computes the offsets, and verifies that every
emitted span actually slices back to its own text. The output format is
deliberately the one ``GLiNER2Trainer`` reads, so the same file becomes training
data in Phase 2 without conversion.

Run with::

    python -m privaparse.evaluation.build_gold
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from privaparse.evaluation import DEFAULT_GOLD_PATH, DEFAULT_GOLD_SOURCE

DEFAULT_SOURCE = DEFAULT_GOLD_SOURCE
DEFAULT_TARGET = DEFAULT_GOLD_PATH

_HEADER_RE = re.compile(r"^###\s+id:\s*(?P<id>\S+)\s*(?:\|\s*kind:\s*(?P<kind>\S+))?\s*$")
_MARKER_RE = re.compile(r"\{\{(?P<type>PERSON|EMAIL|PHONE):(?P<text>[^{}]+)\}\}")

VALID_TYPES = {"PERSON", "EMAIL", "PHONE"}


@dataclass
class Document:
    id: str
    kind: str
    text: str
    entities: list[dict]

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "entities": self.entities,
        }


def parse_source(source: str) -> list[Document]:
    documents: list[Document] = []
    current_id: str | None = None
    current_kind = "unspecified"
    buffer: list[str] = []

    def flush() -> None:
        if current_id is None:
            return
        raw = "".join(buffer).strip("\n")
        if not raw:
            raise ValueError(f"document {current_id} is empty")
        documents.append(_compile_document(current_id, current_kind, raw))

    for line in source.splitlines(keepends=True):
        header = _HEADER_RE.match(line.rstrip("\n"))
        if header:
            flush()
            current_id = header.group("id")
            current_kind = header.group("kind") or "unspecified"
            buffer = []
            continue
        if current_id is not None:
            buffer.append(line)

    flush()
    return documents


def _compile_document(doc_id: str, kind: str, annotated: str) -> Document:
    text_parts: list[str] = []
    entities: list[dict] = []
    cursor = 0
    plain_length = 0

    for match in _MARKER_RE.finditer(annotated):
        before = annotated[cursor : match.start()]
        text_parts.append(before)
        plain_length += len(before)

        surface = match.group("text")
        entities.append(
            {
                "start": plain_length,
                "end": plain_length + len(surface),
                "type": match.group("type"),
                "text": surface,
            }
        )
        text_parts.append(surface)
        plain_length += len(surface)
        cursor = match.end()

    text_parts.append(annotated[cursor:])
    text = "".join(text_parts)

    _validate(doc_id, text, entities)
    return Document(id=doc_id, kind=kind, text=text, entities=entities)


def _validate(doc_id: str, text: str, entities: list[dict]) -> None:
    if "{{" in text or "}}" in text:
        raise ValueError(f"{doc_id}: unparsed marker braces left in the text")

    for entity in entities:
        sliced = text[entity["start"] : entity["end"]]
        if sliced != entity["text"]:
            raise ValueError(
                f"{doc_id}: offset mismatch — text[{entity['start']}:{entity['end']}] "
                f"is {sliced!r}, expected {entity['text']!r}"
            )
        if entity["type"] not in VALID_TYPES:
            raise ValueError(f"{doc_id}: unknown entity type {entity['type']!r}")

    ordered = sorted(entities, key=lambda e: e["start"])
    for left, right in zip(ordered, ordered[1:]):
        if left["end"] > right["start"]:
            raise ValueError(f"{doc_id}: overlapping annotations at {left['start']}")


def write_jsonl(documents: list[Document], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for document in documents:
            handle.write(json.dumps(document.to_json(), ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args(argv)

    documents = parse_source(args.source.read_text(encoding="utf-8"))
    if not documents:
        print(f"no documents found in {args.source}", file=sys.stderr)
        return 1

    write_jsonl(documents, args.target)

    counts: dict[str, int] = {}
    for document in documents:
        for entity in document.entities:
            counts[entity["type"]] = counts.get(entity["type"], 0) + 1

    empty = sum(1 for d in documents if not d.entities)
    print(f"wrote {len(documents)} document(s) to {args.target}")
    for entity_type, count in sorted(counts.items()):
        print(f"  {entity_type:<7} {count}")
    print(f"  {'(none)':<7} {empty} document(s) with no entities at all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
