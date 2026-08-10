"""Measure detection quality against the German gold set.

The question this exists to answer is "do we need to fine-tune GLiNER2 for
German?", and the answer is only worth anything if the threshold was fixed
*before* the numbers were seen. It is:

    PERSON, partial match: recall >= 0.90 and precision >= 0.85

Recall carries more weight than precision on purpose. A missed name is sent to
the LLM — an actual disclosure. A spurious one only costs readability.

EMAIL and PHONE come from rules rather than the model, so they act as a control
group: if they sit near 1.0 and PERSON does not, the model is the problem and
not the pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol, Sequence

from privaparse.evaluation import DEFAULT_GOLD_PATH
from privaparse.parser.types import Span

GOLD_PATH = DEFAULT_GOLD_PATH

#: Fixed in advance. See the module docstring.
PERSON_RECALL_FLOOR = 0.90
PERSON_PRECISION_FLOOR = 0.85

ENTITY_TYPES = ("PERSON", "EMAIL", "PHONE")


class SupportsDetect(Protocol):
    def detect(self, text: str) -> list[Span]: ...


@dataclass(frozen=True)
class GoldEntity:
    start: int
    end: int
    type: str
    text: str

    def overlaps(self, other: "GoldEntity") -> bool:
        return self.start < other.end and other.start < self.end

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class GoldDocument:
    id: str
    kind: str
    text: str
    entities: tuple[GoldEntity, ...]


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        denominator = self.tp + self.fp
        return self.tp / denominator if denominator else 1.0

    @property
    def recall(self) -> float:
        denominator = self.tp + self.fn
        return self.tp / denominator if denominator else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def support(self) -> int:
        return self.tp + self.fn


@dataclass
class Mistake:
    document_id: str
    entity_type: str
    text: str
    start: int
    end: int
    context: str


@dataclass
class EvalReport:
    label: str
    documents: int
    exact: dict[str, Counts] = field(default_factory=dict)
    partial: dict[str, Counts] = field(default_factory=dict)
    false_positives: list[Mistake] = field(default_factory=list)
    false_negatives: list[Mistake] = field(default_factory=list)

    @property
    def person_partial(self) -> Counts:
        return self.partial.get("PERSON", Counts())

    @property
    def needs_finetuning(self) -> bool:
        counts = self.person_partial
        return (
            counts.recall < PERSON_RECALL_FLOOR
            or counts.precision < PERSON_PRECISION_FLOOR
        )

    def verdict(self) -> str:
        counts = self.person_partial
        if not counts.support:
            return "no PERSON entities in the gold set — nothing to decide"
        if self.needs_finetuning:
            reasons = []
            if counts.recall < PERSON_RECALL_FLOOR:
                reasons.append(f"recall {counts.recall:.3f} < {PERSON_RECALL_FLOOR}")
            if counts.precision < PERSON_PRECISION_FLOOR:
                reasons.append(f"precision {counts.precision:.3f} < {PERSON_PRECISION_FLOOR}")
            return "FINE-TUNING WARRANTED — PERSON " + " and ".join(reasons)
        return (
            f"threshold met — PERSON recall {counts.recall:.3f}, "
            f"precision {counts.precision:.3f}; fine-tuning not required"
        )


# --- loading ---------------------------------------------------------------


def load_gold(path: Path = GOLD_PATH) -> list[GoldDocument]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — build it first with: python eval/build_gold.py"
        )
    documents: list[GoldDocument] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            documents.append(
                GoldDocument(
                    id=raw["id"],
                    kind=raw.get("kind", "unspecified"),
                    text=raw["text"],
                    entities=tuple(
                        GoldEntity(e["start"], e["end"], e["type"], e["text"])
                        for e in raw["entities"]
                    ),
                )
            )
    return documents


# --- evaluation ------------------------------------------------------------


def evaluate(
    detector: SupportsDetect,
    documents: Sequence[GoldDocument],
    *,
    label: str = "detector",
) -> EvalReport:
    report = EvalReport(label=label, documents=len(documents))
    for entity_type in ENTITY_TYPES:
        report.exact[entity_type] = Counts()
        report.partial[entity_type] = Counts()

    for document in documents:
        predicted = [_as_gold(span) for span in detector.detect(document.text)]
        for entity_type in ENTITY_TYPES:
            gold_of_type = [e for e in document.entities if e.type == entity_type]
            pred_of_type = [e for e in predicted if e.type == entity_type]

            _score(report.exact[entity_type], gold_of_type, pred_of_type, exact=True)
            matched_gold, matched_pred = _score(
                report.partial[entity_type], gold_of_type, pred_of_type, exact=False
            )

            for index, entity in enumerate(pred_of_type):
                if index not in matched_pred:
                    report.false_positives.append(_mistake(document, entity))
            for index, entity in enumerate(gold_of_type):
                if index not in matched_gold:
                    report.false_negatives.append(_mistake(document, entity))

    return report


def _score(
    counts: Counts,
    gold: Sequence[GoldEntity],
    predicted: Sequence[GoldEntity],
    *,
    exact: bool,
) -> tuple[set[int], set[int]]:
    """Greedy one-to-one matching. Returns the matched gold/predicted indices."""
    matched_gold: set[int] = set()
    matched_pred: set[int] = set()

    for gold_index, gold_entity in enumerate(gold):
        best_index: int | None = None
        best_overlap = 0

        for pred_index, pred_entity in enumerate(predicted):
            if pred_index in matched_pred:
                continue
            if exact:
                if (pred_entity.start, pred_entity.end) == (gold_entity.start, gold_entity.end):
                    best_index = pred_index
                    break
                continue
            overlap = min(gold_entity.end, pred_entity.end) - max(
                gold_entity.start, pred_entity.start
            )
            if overlap > best_overlap:
                best_overlap, best_index = overlap, pred_index

        if best_index is not None:
            matched_gold.add(gold_index)
            matched_pred.add(best_index)

    counts.tp += len(matched_gold)
    counts.fn += len(gold) - len(matched_gold)
    counts.fp += len(predicted) - len(matched_pred)
    return matched_gold, matched_pred


def _as_gold(span: Span) -> GoldEntity:
    return GoldEntity(start=span.start, end=span.end, type=str(span.type), text=span.text)


def _mistake(document: GoldDocument, entity: GoldEntity) -> Mistake:
    window = 30
    start = max(0, entity.start - window)
    end = min(len(document.text), entity.end + window)
    context = document.text[start:end].replace("\n", " ")
    return Mistake(
        document_id=document.id,
        entity_type=entity.type,
        text=entity.text,
        start=entity.start,
        end=entity.end,
        context=f"…{context}…",
    )


# --- reporting -------------------------------------------------------------


def format_report(reports: Iterable[EvalReport], *, show_mistakes: int = 15) -> str:
    lines: list[str] = ["# Evaluation report", ""]
    reports = list(reports)

    lines.append("| Run | Type | Support | P (exact) | R (exact) | P (partial) | R (partial) | F1 (partial) |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for report in reports:
        for entity_type in ENTITY_TYPES:
            exact = report.exact.get(entity_type, Counts())
            partial = report.partial.get(entity_type, Counts())
            lines.append(
                f"| {report.label} | {entity_type} | {partial.support} | "
                f"{exact.precision:.3f} | {exact.recall:.3f} | "
                f"{partial.precision:.3f} | {partial.recall:.3f} | {partial.f1:.3f} |"
            )
    lines.append("")

    lines.append("## Verdict")
    lines.append("")
    lines.append(
        f"Threshold fixed in advance: PERSON partial-match recall >= "
        f"{PERSON_RECALL_FLOOR}, precision >= {PERSON_PRECISION_FLOOR}."
    )
    lines.append("")
    for report in reports:
        lines.append(f"- **{report.label}** — {report.verdict()}")
    lines.append("")

    for report in reports:
        if not (report.false_negatives or report.false_positives):
            continue
        lines.append(f"## Mistakes: {report.label}")
        lines.append("")
        lines.extend(
            _mistake_section(
                "Missed (false negatives — these reach the LLM)",
                report.false_negatives,
                show_mistakes,
            )
        )
        lines.extend(
            _mistake_section(
                "Spurious (false positives — these only cost readability)",
                report.false_positives,
                show_mistakes,
            )
        )

    return "\n".join(lines) + "\n"


def _mistake_section(title: str, mistakes: list[Mistake], limit: int) -> list[str]:
    if not mistakes:
        return [f"### {title}", "", "None.", ""]

    lines = [f"### {title} — {len(mistakes)} total", ""]
    for mistake in mistakes[:limit]:
        lines.append(
            f"- `{mistake.document_id}` **{mistake.entity_type}** "
            f"{mistake.text!r} — {mistake.context}"
        )
    if len(mistakes) > limit:
        lines.append(f"- …and {len(mistakes) - limit} more")
    lines.append("")
    return lines
