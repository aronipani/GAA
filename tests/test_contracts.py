"""Tests for the shared types and their JSONL encoding.
Deliberately not: testing arms, verification, or scoring - those own their own files.
"""

from __future__ import annotations

import json

import pytest

from src.contracts import (
    SHAPES,
    ArmReport,
    ContractError,
    LogRecord,
    make_line_id,
    new_record,
    read_jsonl,
    record_from_dict,
    record_to_dict,
    report_to_dict,
    write_jsonl,
    write_report,
)
from tests.fixtures import (
    APACHE_CLF,
    JSON_LINE_ESCAPED_QUOTES,
    SYSLOG,
    TRUNCATED_LINE,
    UNICODE_LINE,
)


def test_line_id_is_stable_and_distinct() -> None:
    assert make_line_id(SYSLOG) == make_line_id(SYSLOG)
    assert make_line_id(SYSLOG) != make_line_id(APACHE_CLF)
    # Whitespace is content: two lines differing only in indentation are different lines.
    assert make_line_id(SYSLOG) != make_line_id(" " + SYSLOG)


def test_line_id_survives_unicode() -> None:
    assert len(make_line_id(UNICODE_LINE)) == 16
    assert make_line_id(UNICODE_LINE) == make_line_id(UNICODE_LINE)


def test_new_record_derives_the_line_id() -> None:
    record = new_record(SYSLOG, {"host": "web-01"}, "syslog", "test")
    assert record.line_id == make_line_id(SYSLOG)


def test_new_record_copies_fields() -> None:
    fields = {"host": "web-01"}
    record = new_record(SYSLOG, fields, "syslog", "test")
    fields["host"] = "mutated"
    assert record.fields["host"] == "web-01"


@pytest.mark.parametrize(
    "raw", [SYSLOG, APACHE_CLF, JSON_LINE_ESCAPED_QUOTES, UNICODE_LINE, TRUNCATED_LINE]
)
def test_round_trip_through_dict(raw: str) -> None:
    record = new_record(raw, {"message": raw}, "unknown", "test")
    assert record_from_dict(record_to_dict(record)) == record


def test_round_trip_through_jsonl_preserves_escapes_and_unicode(tmp_path) -> None:
    records = [
        new_record(
            JSON_LINE_ESCAPED_QUOTES, {"message": 'he said \\"hi\\"'}, "json_lines", "t"
        ),
        new_record(UNICODE_LINE, {"message": "sauvegarde échouée ✗"}, "logfmt", "t"),
    ]
    path = tmp_path / "out.jsonl"
    write_jsonl(path, records)
    assert read_jsonl(path) == records
    # One record per line, no pretty-printing: the format must stay grep-able.
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_read_jsonl_skips_blank_lines(tmp_path) -> None:
    record = new_record(SYSLOG, {}, "syslog", "t")
    path = tmp_path / "out.jsonl"
    path.write_text(
        "\n" + json.dumps(record_to_dict(record)) + "\n\n", encoding="utf-8"
    )
    assert read_jsonl(path) == [record]


def test_read_jsonl_reports_the_offending_line_number(tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    good = json.dumps(record_to_dict(new_record(SYSLOG, {}, "syslog", "t")))
    path.write_text(good + "\n{not json\n", encoding="utf-8")
    with pytest.raises(ContractError, match=r"bad\.jsonl:2"):
        read_jsonl(path)


def _payload(**overrides: object) -> dict:
    base = {"raw": "x", "fields": {}, "shape": "syslog", "source": "t", "line_id": "a"}
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({k: v for k, v in _payload().items() if k != "line_id"}, "missing key"),
        (_payload(z=1), "unknown key"),
        (_payload(raw=1), "raw must be"),
        (_payload(fields=[]), "must be an object"),
        (_payload(fields={"status": 200}), "str->str"),
        (_payload(shape=7), "shape must be"),
    ],
)
def test_record_from_dict_rejects_malformed_payloads(payload: dict, expected: str) -> None:
    with pytest.raises(ContractError, match=expected):
        record_from_dict(payload)


def test_fields_must_be_strings_not_numbers() -> None:
    """Numeric status codes are the most likely accidental type drift between arms."""
    payload = record_to_dict(new_record(APACHE_CLF, {"status": "200"}, "apache_clf", "t"))
    payload["fields"]["status"] = 200
    with pytest.raises(ContractError):
        record_from_dict(payload)


def test_shapes_vocabulary_is_the_documented_one() -> None:
    assert SHAPES == (
        "syslog",
        "json_lines",
        "logfmt",
        "apache_clf",
        "multiline_trace",
        "unknown",
    )


def _toy_report() -> ArmReport:
    return ArmReport(
        arm="arm1_classical",
        field_precision=0.5,
        field_recall=0.25,
        field_f1=1 / 3,
        exact_match=0.0,
        schema_compliance=1.0,
        per_shape_f1={"syslog": 0.5},
        unseen_shape_f1=0.0,
        label_tokens_in=0,
        label_tokens_out=0,
        label_wall_clock_s=1.5,
        seed=1337,
        notes="toy",
    )


def test_report_has_every_comparison_field() -> None:
    """Arms reporting different fields cannot be compared; that is the whole point."""
    assert set(report_to_dict(_toy_report())) == {
        "arm",
        "field_precision",
        "field_recall",
        "field_f1",
        "exact_match",
        "schema_compliance",
        "per_shape_f1",
        "unseen_shape_f1",
        "label_tokens_in",
        "label_tokens_out",
        "label_wall_clock_s",
        "seed",
        "notes",
    }


def test_write_report_creates_parents_and_valid_json(tmp_path) -> None:
    path = tmp_path / "reports" / "arm1.json"
    write_report(path, _toy_report())
    assert json.loads(path.read_text(encoding="utf-8")) == report_to_dict(_toy_report())


def test_logrecord_equality_ignores_construction_route() -> None:
    manual = LogRecord(
        raw=SYSLOG, fields={}, shape="syslog", source="t", line_id=make_line_id(SYSLOG)
    )
    assert manual == new_record(SYSLOG, {}, "syslog", "t")
