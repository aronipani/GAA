"""Checks whether a record is WELL FORMED: known fields, right types, required ones present.
Deliberately not: whether it is CORRECT - that is exact.py, and the split is the point.

STUB - step 4. Read `docs/lessons/step4.md`, then make tests/test_verify_schema.py pass.
Read src/contracts.py:record_from_dict first: it is the same discipline on a smaller problem.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.contracts import LogRecord, Shape, VerifyResult

# The non-standard keyword in configs/schema.json holding the per-shape required sets.
# JSON Schema validators ignore unknown keywords, so the file stays a valid schema.
REQUIRED_BY_SHAPE_KEY = "x-required-by-shape"


class SchemaConfigError(ValueError):
    """configs/schema.json is malformed or disagrees with contracts.SHAPES."""


@dataclass(frozen=True)
class FieldSchema:
    """A compiled validator plus the per-shape required sets pulled out of the same file."""

    validator: Any  # jsonschema.protocols.Validator
    required_by_shape: dict[Shape, tuple[str, ...]]


def load_field_schema(path: Path) -> FieldSchema:
    """Load configs/schema.json into a FieldSchema.

    Must raise SchemaConfigError if the required-by-shape table names a shape that is not
    in contracts.SHAPES - otherwise a typo there silently means "this shape requires
    nothing" and every record of that shape passes validation vacuously.
    """
    raise NotImplementedError("step 4")


def verify_schema(record: LogRecord, schema: FieldSchema) -> VerifyResult:
    """Structural check only. Never reads gold, never judges whether a value is right.

    Reports EVERY failure, not just the first, each naming the field it concerns, in a
    deterministic order so two runs produce comparable output.
    """
    raise NotImplementedError("step 4")


def verify_many(records: list[LogRecord], schema: FieldSchema) -> list[VerifyResult]:
    """Same order as the input."""
    raise NotImplementedError("step 4")
