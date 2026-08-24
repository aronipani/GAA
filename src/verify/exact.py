"""Checks whether a record is CORRECT: exact field-value match against the gold answer key.
Deliberately not: fuzzy matching, normalisation, or any notion of "close enough".

STUB - step 4. Read `docs/lessons/step4.md`, then make tests/test_verify_exact.py pass.
This is the layer that catches what schema.py structurally cannot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.contracts import LogRecord, VerifyResult


@dataclass(frozen=True)
class FieldDiff:
    """Per-record tallies. The eval harness sums these; nothing else derives from them."""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    wrong: tuple[tuple[str, str, str], ...] = ()  # (field, got, want)
    missing: tuple[str, ...] = field(default=())  # in gold, absent from prediction
    extra: tuple[str, ...] = field(default=())  # in prediction, absent from gold


def compare(predicted: LogRecord, gold: LogRecord) -> tuple[VerifyResult, FieldDiff]:
    """Exact string comparison, field by field.

    A wrong value costs both a false positive and a false negative: it is one hallucinated
    field plus one missed field, and charging it once would flatter precision.

    Mismatched line_id raises ContractError - that is a join bug, not a wrong answer, and
    scoring it as a miss would hide it.
    """
    raise NotImplementedError("step 4")


def compare_many(
    predicted: list[LogRecord], gold: list[LogRecord]
) -> list[tuple[VerifyResult, FieldDiff]]:
    """Pair by line_id, not by position. Returns results in gold order.

    A prediction with no gold counterpart is an error, not something to skip quietly.
    """
    raise NotImplementedError("step 4")
