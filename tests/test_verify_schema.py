"""Step 4, part 1. These tests are the assignment: make them pass.
Deliberately not: testing correctness against gold - that is test_verify_exact.py.

The one to understand before you start is
test_schema_cannot_catch_a_semantically_swapped_record. A verifier that fails it is
"better" at spotting errors and useless for this project, because it collapses two
different failures into one number.
"""

from __future__ import annotations

import json

import pytest

from src.config import CONFIGS_DIR
from src.contracts import SHAPES, new_record
from src.verify.schema import (
    REQUIRED_BY_SHAPE_KEY,
    SchemaConfigError,
    load_field_schema,
    verify_many,
    verify_schema,
)
from tests.fixtures import (
    SYSLOG,
    SYSLOG_SWAPPED_FIELDS,
    UNICODE_LINE,
    VALID_FIELDS,
)

pytestmark = pytest.mark.lesson

SCHEMA_PATH = CONFIGS_DIR / "schema.json"


@pytest.fixture
def schema():
    return load_field_schema(SCHEMA_PATH)


def record(shape: str, fields: dict[str, str], raw: str = SYSLOG):
    return new_record(raw, fields, shape, "test")


# --- loading -------------------------------------------------------------------------

def test_loads_the_shipped_schema(schema) -> None:
    assert set(schema.required_by_shape) <= set(SHAPES)
    assert "timestamp" in schema.required_by_shape["syslog"]


def test_required_table_naming_an_unknown_shape_is_an_error(tmp_path) -> None:
    """A typo here would silently mean 'this shape requires nothing'."""
    doc = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    doc[REQUIRED_BY_SHAPE_KEY]["sysog"] = ["timestamp"]
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SchemaConfigError, match="sysog"):
        load_field_schema(path)


def test_comment_keys_in_the_required_table_are_ignored(schema) -> None:
    assert "_comment" not in schema.required_by_shape


# --- the happy path ------------------------------------------------------------------

@pytest.mark.parametrize("shape", sorted(VALID_FIELDS))
def test_a_valid_record_passes_for_every_shape(schema, shape: str) -> None:
    result = verify_schema(record(shape, VALID_FIELDS[shape]), schema)
    assert result.passed, result.failures
    assert result.failures == []


def test_unicode_values_pass(schema) -> None:
    fields = {**VALID_FIELDS["logfmt"], "message": "sauvegarde échouée ✗", "host": "café-01"}
    assert verify_schema(record("logfmt", fields, UNICODE_LINE), schema).passed


def test_escaped_quotes_in_a_value_pass(schema) -> None:
    fields = {**VALID_FIELDS["json_lines"], "message": 'upstream said "bad \\"gateway\\""'}
    assert verify_schema(record("json_lines", fields), schema).passed


# --- structural failures -------------------------------------------------------------

@pytest.mark.parametrize(
    "shape,override,expect",
    [
        ("apache_clf", {"status": "20"}, "status"),        # pattern: three digits
        ("apache_clf", {"status": "600"}, "status"),       # pattern: must start 1-5
        ("apache_clf", {"protocol": "HTTP1.1"}, "protocol"),
        ("apache_clf", {"bytes": "4.2kb"}, "bytes"),
        ("apache_clf", {"method": "FETCH"}, "method"),     # enum
        ("json_lines", {"level": "LOUD"}, "level"),        # enum
        ("syslog", {"pid": "44a2"}, "pid"),                # pattern: digits only
        ("syslog", {"host": ""}, "host"),                  # minLength
    ],
)
def test_a_malformed_value_fails_and_names_its_field(
    schema, shape: str, override: dict, expect: str
) -> None:
    result = verify_schema(record(shape, {**VALID_FIELDS[shape], **override}), schema)
    assert not result.passed
    assert any(expect in f for f in result.failures)


def test_an_unknown_field_fails(schema) -> None:
    """additionalProperties is false: the vocabulary is closed on purpose."""
    fields = {**VALID_FIELDS["syslog"], "trace_id": "abc123"}
    result = verify_schema(record("syslog", fields), schema)
    assert not result.passed
    assert any("trace_id" in f for f in result.failures)


@pytest.mark.parametrize("shape,drop", [("syslog", "host"), ("apache_clf", "status")])
def test_a_missing_required_field_fails(schema, shape: str, drop: str) -> None:
    fields = {k: v for k, v in VALID_FIELDS[shape].items() if k != drop}
    result = verify_schema(record(shape, fields), schema)
    assert not result.passed
    assert any(drop in f for f in result.failures)


def test_an_empty_required_field_counts_as_missing(schema) -> None:
    """A parser that emits "" for a field it could not find has still not found it."""
    fields = {**VALID_FIELDS["syslog"], "message": ""}
    assert not verify_schema(record("syslog", fields), schema).passed


def test_an_unknown_shape_fails_without_raising(schema) -> None:
    result = verify_schema(record("k8s_json", VALID_FIELDS["json_lines"]), schema)
    assert not result.passed
    assert any("k8s_json" in f for f in result.failures)


def test_an_unknown_shape_with_valid_fields_still_fails(schema) -> None:
    """The shape is part of the contract; an arm inventing one must not slip through."""
    assert not verify_schema(record("", VALID_FIELDS["syslog"]), schema).passed


# --- the property the whole project rests on -----------------------------------------

def test_schema_cannot_catch_a_semantically_swapped_record(schema) -> None:
    """host and process swapped. Both are free-form strings, so this is well formed
    and completely wrong. schema.py MUST pass it; exact.py is what catches it.

    If you 'fix' this by making schema.py smarter, schema_compliance and field_f1 stop
    measuring different things and the report loses the distinction it exists to show.
    """
    result = verify_schema(record("syslog", SYSLOG_SWAPPED_FIELDS), schema)
    assert result.passed
    assert result.failures == []


def test_an_enum_does_catch_a_swap_between_enum_fields(schema) -> None:
    """level="GET" is not schema-valid here, because level is an enum. Contrast with the
    test above: how much a swap costs depends on how constrained the fields are."""
    fields = {"level": "GET", "method": "INFO", "message": "x", "timestamp": "2026-08-22"}
    assert not verify_schema(record("json_lines", fields), schema).passed


# --- contract of the result ----------------------------------------------------------

def test_passed_is_true_exactly_when_there_are_no_failures(schema) -> None:
    good = verify_schema(record("syslog", VALID_FIELDS["syslog"]), schema)
    bad = verify_schema(record("syslog", {}), schema)
    assert good.passed and good.failures == []
    assert not bad.passed and bad.failures


def test_every_failure_is_reported_not_just_the_first(schema) -> None:
    fields = {**VALID_FIELDS["apache_clf"], "status": "20", "method": "FETCH", "bytes": "x"}
    assert len(verify_schema(record("apache_clf", fields), schema).failures) >= 3


def test_failure_order_is_deterministic(schema) -> None:
    fields = {**VALID_FIELDS["apache_clf"], "status": "20", "method": "FETCH", "bytes": "x"}
    first = verify_schema(record("apache_clf", fields), schema).failures
    second = verify_schema(record("apache_clf", dict(fields)), schema).failures
    assert first == second


def test_verify_many_preserves_order(schema) -> None:
    records = [
        record("syslog", VALID_FIELDS["syslog"]),
        record("syslog", {}, raw="second"),
        record("logfmt", VALID_FIELDS["logfmt"], raw="third"),
    ]
    assert [r.passed for r in verify_many(records, schema)] == [True, False, True]
