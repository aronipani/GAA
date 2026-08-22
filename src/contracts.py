"""The types every module in this repo shares, plus the JSONL encoding for them.
Deliberately not: validation, scoring, or I/O policy - those live in verify/ and eval/.

On-disk schema for a LogRecord line (one JSON object per line, UTF-8, no trailing comma):
    {"raw": str, "fields": {str: str}, "shape": str, "source": str, "line_id": str}
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final, Protocol

# "syslog" | "json_lines" | "logfmt" | "apache_clf" | "multiline_trace" | "unknown"
Shape = str

SHAPES: Final[tuple[Shape, ...]] = (
    "syslog",
    "json_lines",
    "logfmt",
    "apache_clf",
    "multiline_trace",
    "unknown",
)

UNKNOWN_SHAPE: Final[Shape] = "unknown"

# Truncated SHA-256. Full-length hashes make JSONL lines noisy to read by eye and
# 16 hex chars is ~10^-10 collision risk at the corpus sizes here (<= 10^6 lines).
LINE_ID_HEX_CHARS: Final[int] = 16


def make_line_id(raw: str) -> str:
    """Stable join key for a raw line. Same text -> same id, on any machine, forever."""
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return digest[:LINE_ID_HEX_CHARS]


def is_valid_shape(shape: str) -> bool:
    return shape in SHAPES


@dataclass
class LogRecord:
    """One log line and its parsed fields. The unit of everything."""

    raw: str
    fields: dict[str, str]
    shape: Shape
    source: str  # which arm or human produced this
    line_id: str  # stable hash of raw; used for joins and dedup


@dataclass
class VerifyResult:
    passed: bool
    failures: list[str]  # human-readable, one per failed check. empty iff passed.


@dataclass
class ArmReport:
    """Every arm emits this. Same fields, always - arms that differ can't be compared."""

    arm: str
    field_precision: float
    field_recall: float
    field_f1: float
    exact_match: float
    schema_compliance: float
    per_shape_f1: dict[Shape, float]
    unseen_shape_f1: float
    label_tokens_in: int
    label_tokens_out: int
    label_wall_clock_s: float
    seed: int
    notes: str = ""


class Arm(Protocol):
    """A labelling technique. Takes raw lines, returns labelled records."""

    name: str

    def label(self, lines: list[str]) -> list[LogRecord]: ...


class ContractError(ValueError):
    """A record on disk does not match the contract above."""


def new_record(raw: str, fields: dict[str, str], shape: Shape, source: str) -> LogRecord:
    """Build a record with a line_id derived from raw, so callers can't invent their own."""
    return LogRecord(raw=raw, fields=dict(fields), shape=shape, source=source,
                     line_id=make_line_id(raw))


def record_to_dict(record: LogRecord) -> dict[str, Any]:
    return asdict(record)


def record_from_dict(payload: dict[str, Any]) -> LogRecord:
    """Strict: a malformed record is a bug upstream, and silently coercing it hides that."""
    expected = {"raw", "fields", "shape", "source", "line_id"}
    missing = expected - payload.keys()
    if missing:
        raise ContractError(f"LogRecord missing key(s): {sorted(missing)}")
    extra = payload.keys() - expected
    if extra:
        raise ContractError(f"LogRecord has unknown key(s): {sorted(extra)}")
    if not isinstance(payload["raw"], str):
        raise ContractError("LogRecord.raw must be a string")
    if not isinstance(payload["fields"], dict):
        raise ContractError("LogRecord.fields must be an object")
    for key, value in payload["fields"].items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ContractError(f"LogRecord.fields must be str->str, got {key!r}: {value!r}")
    for key in ("shape", "source", "line_id"):
        if not isinstance(payload[key], str):
            raise ContractError(f"LogRecord.{key} must be a string")
    return LogRecord(
        raw=payload["raw"],
        fields=dict(payload["fields"]),
        shape=payload["shape"],
        source=payload["source"],
        line_id=payload["line_id"],
    )


def write_jsonl(path: Path, records: list[LogRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record_to_dict(record), ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[LogRecord]:
    records: list[LogRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError(f"{path}:{lineno} is not valid JSON: {exc}") from exc
            try:
                records.append(record_from_dict(payload))
            except ContractError as exc:
                raise ContractError(f"{path}:{lineno} {exc}") from exc
    return records


def report_to_dict(report: ArmReport) -> dict[str, Any]:
    return asdict(report)


def write_report(path: Path, report: ArmReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report_to_dict(report), indent=2) + "\n", encoding="utf-8")
