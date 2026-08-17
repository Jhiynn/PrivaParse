"""Measure detection quality against the German gold set.

The question this exists to answer is "do we need to fine-tune GLiNER2 for
German?", and the answer is only worth anything if the bar was fixed
*before* the numbers were seen. Each type's bar now lives in the catalogue
(the `bar:` key in `privaparse/app/entities.default.yaml`), not here, so
widening the catalogue never means widening this module to match — it already
scores whatever the catalogue enables against whatever bar it declares.

Recall carries more weight than precision on purpose. A missed name is sent to
the LLM — an actual disclosure. A spurious one only costs readability.

EMAIL and PHONE come from rules rather than the model, so they act as a control
group: if they sit near 1.0 and PERSON does not, the model is the problem and
not the pipeline.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from privaparse.evaluation import DEFAULT_GOLD_PATH
from privaparse.parser.types import Span

if TYPE_CHECKING:  # pragma: no cover
    from privaparse.app.catalogue import Catalogue
    from privaparse.parser.detection_pass import DetectionPass
    from privaparse.parser.markdown import ProtectedText

GOLD_PATH = DEFAULT_GOLD_PATH


@dataclass(frozen=True)
class GoldEntity:
    start: int
    end: int
    type: str
    text: str
    #: The model label the prediction this stands in for came from. Only ever
    #: set on the predicted side of a match (see `_as_gold`) — an actual gold
    #: annotation has no model label, since it was hand-written, not detected.
    label: str | None = None

    def overlaps(self, other: GoldEntity) -> bool:
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
    #: The catalogue this run was scored against — where every type's bar
    #: comes from. Optional only so a test can build a report by hand without
    #: one; `evaluate()` always sets it.
    catalogue: Catalogue | None = None
    # Per-label recall is not computable and must not be reported: gold
    # entities carry a placeholder type, not a model label, so a missed
    # entity has no label to attribute it to, and there is no denominator to
    # divide by. This table carries TP, FP and precision only.
    by_label: dict[str, Counts] = field(default_factory=dict)

    @property
    def person_partial(self) -> Counts:
        return self.partial.get("PERSON", Counts())

    @property
    def needs_finetuning(self) -> bool:
        """Thin wrapper over :meth:`verdicts`, kept because main.py's CLI
        summary still asks this one question — the question this project
        exists to answer — ahead of printing the full per-type table."""
        for name, ok, _ in self.verdicts():
            if name == "PERSON":
                return not ok
        return False

    def verdict(self) -> str:
        """PERSON's verdict, spelled out as one sentence. A thin wrapper over
        :meth:`verdicts`, kept for the same reason as `needs_finetuning`.

        Zero gold support is checked first and returns its own sentence,
        separately from the bar comparison below: "no data" is not the same
        claim as "passed", and the two must not be squashed into one
        sentence that appends a conclusion ("fine-tuning not required") to a
        case where nothing was actually measured.
        """
        if not self.person_partial.support:
            return "no PERSON entities in the gold set — nothing to decide"
        for name, ok, explanation in self.verdicts():
            if name != "PERSON":
                continue
            if ok:
                return f"PERSON {explanation} — fine-tuning not required"
            return f"FINE-TUNING WARRANTED — PERSON {explanation}"
        return "no PERSON bar in the catalogue — nothing to decide"

    def verdicts(self) -> list[tuple[str, bool, str]]:
        """(type, meets_bar, explanation) for every type that declares a bar.

        Types without a bar are absent rather than reported as passing. A
        silent pass on an unmeasured type is exactly the claim this project
        exists not to make.
        """
        if self.catalogue is None:
            return []
        out: list[tuple[str, bool, str]] = []
        for placeholder in self.catalogue.enabled:
            bar = placeholder.bar
            if bar is None:
                continue
            counts = self.partial.get(placeholder.name, Counts())
            if not counts.support:
                out.append((placeholder.name, True, "no gold entities — nothing measured"))
                continue
            reasons = []
            if bar.recall is not None and counts.recall < bar.recall:
                reasons.append(f"recall {counts.recall:.3f} < {bar.recall}")
            if bar.precision is not None and counts.precision < bar.precision:
                reasons.append(f"precision {counts.precision:.3f} < {bar.precision}")
            if reasons:
                out.append((placeholder.name, False, "under bar — " + " and ".join(reasons)))
            else:
                out.append((
                    placeholder.name, True,
                    f"meets bar — recall {counts.recall:.3f}, precision {counts.precision:.3f}",
                ))
        return out


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
    spans: Sequence[Sequence[Span]],
    documents: Sequence[GoldDocument],
    *,
    label: str = "detector",
    catalogue: Catalogue,
) -> EvalReport:
    """Score already-detected spans against the gold set.

    ``spans`` carries one list per document in ``documents``, positionally —
    this is a scorer, and detecting is the caller's job. It deliberately takes
    neither a detector nor a :class:`DetectionPass`: several of this
    function's own tests feed span sets a merge would never produce (two
    overlapping spans of one type, asserting a single true positive), so a
    signature that ran its inputs through a pass first would delete the very
    thing those tests assert about. See ADR-0004, and
    ``test_two_predictions_cannot_both_claim_one_gold_entity``.

    ``strict=True`` because a span list per document is the whole invariant
    once the spans are pushed in rather than pulled: a short or long sequence
    would score one document's spans against another's gold entities.
    """
    report = EvalReport(label=label, documents=len(documents), catalogue=catalogue)
    entity_types = [t.name for t in catalogue.enabled]
    for entity_type in entity_types:
        report.exact[entity_type] = Counts()
        report.partial[entity_type] = Counts()

    for document, document_spans in zip(documents, spans, strict=True):
        predicted = [_as_gold(span) for span in document_spans]
        for entity_type in entity_types:
            gold_of_type = [e for e in document.entities if e.type == entity_type]
            pred_of_type = [e for e in predicted if e.type == entity_type]

            _score(report.exact[entity_type], gold_of_type, pred_of_type, exact=True)
            matched_gold, matched_pred = _score(
                report.partial[entity_type], gold_of_type, pred_of_type, exact=False
            )

            for index, entity in enumerate(pred_of_type):
                # "(rule)" covers backstop and coreference-sweep spans, which
                # never carry a model label — grouping them under one key
                # keeps the per-label table from growing a blank row.
                counts = report.by_label.setdefault(entity.label or "(rule)", Counts())
                if index in matched_pred:
                    counts.tp += 1
                else:
                    counts.fp += 1
                    report.false_positives.append(_mistake(document, entity))
            for index, entity in enumerate(gold_of_type):
                if index not in matched_gold:
                    report.false_negatives.append(_mistake(document, entity))

    return report


def detect_for_scoring(
    detection: DetectionPass, documents: Sequence[GoldDocument], *, batched: bool
) -> list[list[Span]]:
    """What to hand :func:`evaluate`: each document's spans, in the order the
    documents arrived.

    ``batched`` is not a performance knob, which is why it has no default —
    the two callers are measuring different things and each has to say which.

    The **benchmark** passes ``True``. Batch size is one of the variables its
    matrix sweeps, and its opening claim is that a configuration which gets
    faster because it stops noticing names is broken rather than optimised.
    A quality column computed one document at a time cannot see that: batch
    size could not move it, so every batch-size row would read identically no
    matter what batching did to recall. Scored batched, the number answers the
    question the row was put there to ask.

    The **eval command** passes ``False``. Batch size is not a variable in the
    fine-tuning question, and the figures that command publishes should not
    move with a setting unrelated to it — one document at a time keeps them
    comparable with every number already recorded under `docs/benchmarks/`.
    """
    if batched:
        return detection.run_batch([document.text for document in documents])
    return [detection.run(document.text) for document in documents]


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
    return GoldEntity(
        start=span.start, end=span.end, type=str(span.type), text=span.text, label=span.label
    )


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
        placeholders = report.catalogue.enabled if report.catalogue is not None else ()
        for placeholder in placeholders:
            entity_type = placeholder.name
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
        "One line per catalogued type that declares a bar (the `bar:` key in "
        "the catalogue). A type with no bar is measured in the table above "
        "but has no line here — an unmeasured type is reported as absent, "
        "never as passing."
    )
    lines.append("")
    for report in reports:
        for name, ok, explanation in report.verdicts():
            marker = "OK" if ok else "FAIL"
            lines.append(f"- **{report.label}** {name} [{marker}] — {explanation}")
    lines.append("")

    lines.append("## Per model label")
    lines.append("")
    lines.append("Recall is not shown: gold entities carry a placeholder type, not a")
    lines.append("model label, so a missed entity has no label to attribute it to.")
    lines.append("")
    lines.append("| Run | Label | TP | FP | Precision |")
    lines.append("| --- | --- | ---: | ---: | ---: |")
    for report in reports:
        for name, counts in sorted(report.by_label.items(), key=lambda kv: -kv[1].fp):
            lines.append(
                f"| {report.label} | {name} | {counts.tp} | {counts.fp} | "
                f"{counts.precision:.3f} |"
            )
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


# --- threshold sweep ---------------------------------------------------------

#: Coarse enough to show the curve's shape, fine enough to read a value off it.
DEFAULT_SWEEP: tuple[float, ...] = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


class SupportsDetectRaw(Protocol):
    """Duck type for the engine `sweep_thresholds` drives — real or fake.

    A `Protocol`, not `PrivaParseEngine` itself: tests exercise this against
    a fake that stands in for the engine without inheriting from it, and this
    module has no reason to import the concrete class just to spell out the
    two things it actually touches.
    """

    settings: Any

    def detect_raw(self, text: str) -> tuple[ProtectedText, list[Span]]: ...


def _without_per_type_thresholds(catalogue: Catalogue) -> Catalogue:
    """The same catalogue, with every type's own ``threshold:`` cleared.

    Looks like it throws away the thing the sweep is supposed to measure;
    it does the opposite. ``merge_spans`` now consults a type's catalogue
    threshold ahead of whatever threshold it is called with (the fix that
    made ``Catalogue.threshold_for`` have a caller at all — see
    ``_threshold_for_span`` in ``merge.py``). Left unstripped, a type that
    already declares a threshold would keep filtering at that one fixed
    value on every point of the sweep, no matter what `thresholds` asks
    for — its precision/recall would read identically at 0.3 and at 0.9,
    a flat line where the whole point is a curve. Stripping puts every type
    back under one shared, genuinely-varying cut for exactly as long as the
    sweep needs one, which is the only sense in which "sweep the threshold"
    can produce a curve to read a value off of. Everything else about each
    type — its validator, its sweep mode, its bar — is untouched; only the
    field this function exists to make irrelevant for the sweep's duration
    is cleared.
    """
    stripped = {
        name: replace(placeholder, threshold=None) for name, placeholder in catalogue.types.items()
    }
    return replace(catalogue, types=stripped)


def sweep_thresholds(
    engine: SupportsDetectRaw,
    documents: Sequence[GoldDocument],
    *,
    thresholds: Sequence[float] = DEFAULT_SWEEP,
    catalogue: Catalogue,
) -> dict[float, EvalReport]:
    """Score the gold set at several thresholds from one model pass.

    The model is the expensive part and its scores do not depend on the
    threshold — merging does. So detection runs once per document, and every
    point on the curve is a re-merge, which is cheap. Filtering the *merged*
    spans instead would be wrong: the threshold changes which candidates
    compete for an overlap, not only which survive it — a span that loses an
    overlap at 0.5 can win it at 0.7 if its rival fell below the new cut.

    Swept against ``_without_per_type_thresholds(catalogue)``, not
    ``catalogue`` itself — see that function for why a per-type threshold
    left in place would silently flatten that type's own curve.

    Each point re-merges the scanned candidates positionally, one entry per
    document, and hands the resulting spans to :func:`evaluate`. Position, not
    text: two gold documents can legitimately carry identical text, and the
    same text scanned twice is not promised to yield the same candidates, so
    anything keyed by text would let one document's result stand in for
    another's.
    """
    from privaparse.parser.merge import resolve_spans

    swept_catalogue = _without_per_type_thresholds(catalogue)

    scanned: list[tuple[ProtectedText, list[Span]]] = [
        engine.detect_raw(document.text) for document in documents
    ]

    sweep_enabled = bool(getattr(engine.settings, "coreference_sweep", True))
    return {
        threshold: evaluate(
            [
                resolve_spans(
                    protected,
                    candidates,
                    threshold=threshold,
                    sweep=sweep_enabled,
                    catalogue=swept_catalogue,
                )
                for protected, candidates in scanned
            ],
            documents,
            label=f"t={threshold:.2f}",
            catalogue=swept_catalogue,
        )
        for threshold in thresholds
    }


def format_sweep(results: dict[float, EvalReport]) -> str:
    """Precision/recall per type across the swept thresholds.

    Counts only — no entity text. This is meant to be pasted into a plan or a
    PR to justify a threshold choice, not a mistakes report.
    """
    if not results:
        return ""
    first = next(iter(results.values()))
    types = [t.name for t in first.catalogue.enabled] if first.catalogue else []

    lines = [
        "## Threshold sweep",
        "",
        "One model pass per document; each row re-merges the same scored spans.",
        "",
        "| Type | Threshold | Support | Precision | Recall | F1 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for entity_type in types:
        for threshold in sorted(results):
            counts = results[threshold].partial.get(entity_type, Counts())
            if not counts.support and not counts.fp:
                continue
            lines.append(
                f"| {entity_type} | {threshold:.2f} | {counts.support} | "
                f"{counts.precision:.3f} | {counts.recall:.3f} | {counts.f1:.3f} |"
            )
    lines.append("")
    return "\n".join(lines)
