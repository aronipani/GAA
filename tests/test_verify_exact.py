"""Step 4, part 2. These tests are the assignment: make them pass.
Deliberately not: testing structure - a record can be perfectly formed and score zero here.

The tally rule is the thing to get right: a wrong value is BOTH a false positive and a
false negative. Charge it once and precision flatters every arm equally, which is worse
than being wrong in an obvious way.
"""

from __future__ import annotations

import pytest

from src.contracts import ContractError, new_record
from src.verify.exact import compare, compare_many
from tests.fixtures import SYSLOG, SYSLOG_SWAPPED_FIELDS, VALID_FIELDS

pytestmark = pytest.mark.lesson

GOLD_FIELDS = VALID_FIELDS["syslog"]


def gold(raw: str = SYSLOG, fields: dict[str, str] | None = None):
    return new_record(raw, GOLD_FIELDS if fields is None else fields, "syslog", "human")


def pred(fields: dict[str, str], raw: str = SYSLOG):
    return new_record(raw, fields, "syslog", "arm_test")


# --- the tallies ---------------------------------------------------------------------

def test_a_perfect_match_scores_everything_true_positive() -> None:
    result, diff = compare(pred(GOLD_FIELDS), gold())
    assert result.passed
    assert result.failures == []
    assert (diff.tp, diff.fp, diff.fn) == (len(GOLD_FIELDS), 0, 0)


def test_a_wrong_value_costs_both_a_false_positive_and_a_false_negative() -> None:
    """One hallucinated field plus one missed field. This is the load-bearing decision."""
    result, diff = compare(pred({**GOLD_FIELDS, "host": "web-02"}), gold())
    assert not result.passed
    assert (diff.tp, diff.fp, diff.fn) == (len(GOLD_FIELDS) - 1, 1, 1)
    assert diff.wrong == (("host", "web-02", "web-01"),)


def test_a_missing_field_is_a_false_negative_only() -> None:
    fields = {k: v for k, v in GOLD_FIELDS.items() if k != "pid"}
    result, diff = compare(pred(fields), gold())
    assert (diff.tp, diff.fp, diff.fn) == (len(GOLD_FIELDS) - 1, 0, 1)
    assert diff.missing == ("pid",)
    assert diff.wrong == ()


def test_an_extra_field_is_a_false_positive_only() -> None:
    result, diff = compare(pred({**GOLD_FIELDS, "thread": "main"}), gold())
    assert (diff.tp, diff.fp, diff.fn) == (len(GOLD_FIELDS), 1, 0)
    assert diff.extra == ("thread",)


def test_an_empty_prediction_misses_everything() -> None:
    result, diff = compare(pred({}), gold())
    assert (diff.tp, diff.fp, diff.fn) == (0, 0, len(GOLD_FIELDS))
    assert not result.passed


# --- the case schema.py cannot see ---------------------------------------------------

def test_the_semantic_swap_that_schema_validation_passes() -> None:
    """Same record as test_schema_cannot_catch_a_semantically_swapped_record.
    There it passes; here it must fail. That contrast is the reason for two modules."""
    result, diff = compare(pred(SYSLOG_SWAPPED_FIELDS), gold())
    assert not result.passed
    assert (diff.fp, diff.fn) == (2, 2)
    assert {w[0] for w in diff.wrong} == {"host", "process"}


# --- exactness means exactness -------------------------------------------------------

@pytest.mark.parametrize("variant", ["web-01 ", " web-01", "WEB-01", "web‑01"])
def test_near_misses_are_misses(variant: str) -> None:
    """Trailing space, case, and a unicode non-breaking hyphen are all different values."""
    _, diff = compare(pred({**GOLD_FIELDS, "host": variant}), gold())
    assert diff.fp == 1 and diff.fn == 1


def test_an_empty_string_is_a_present_field_not_an_absent_one() -> None:
    """contracts.py forbids null, so "" is the only way to say 'found, but blank'."""
    _, diff = compare(pred({**GOLD_FIELDS, "host": ""}), gold())
    assert diff.missing == ()
    assert diff.wrong == (("host", "", "web-01"),)


def test_both_sides_empty_is_a_pass() -> None:
    result, diff = compare(pred({}), gold(fields={}))
    assert result.passed
    assert (diff.tp, diff.fp, diff.fn) == (0, 0, 0)


# --- joins ---------------------------------------------------------------------------

def test_mismatched_line_id_raises_rather_than_scoring_zero() -> None:
    """A join bug scored as a miss looks exactly like a bad model. It must be loud."""
    with pytest.raises(ContractError):
        compare(pred(GOLD_FIELDS, raw="a different line"), gold())


def test_compare_many_pairs_by_line_id_not_position() -> None:
    golds = [gold(raw="line-a"), gold(raw="line-b"), gold(raw="line-c")]
    shuffled = [pred(GOLD_FIELDS, raw=r) for r in ("line-c", "line-a", "line-b")]
    results = compare_many(shuffled, golds)
    assert len(results) == 3
    assert all(r.passed for r, _ in results)


def test_compare_many_returns_results_in_gold_order() -> None:
    golds = [gold(raw="line-a"), gold(raw="line-b")]
    shuffled = [
        pred({**GOLD_FIELDS, "host": "wrong"}, raw="line-b"),
        pred(GOLD_FIELDS, raw="line-a"),
    ]
    assert [r.passed for r, _ in compare_many(shuffled, golds)] == [True, False]


def test_a_prediction_with_no_gold_counterpart_is_an_error() -> None:
    with pytest.raises(ContractError):
        compare_many([pred(GOLD_FIELDS, raw="orphan")], [gold(raw="line-a")])


def test_a_gold_record_with_no_prediction_is_an_error() -> None:
    """Silently dropping it would inflate every score by shrinking the denominator."""
    with pytest.raises(ContractError):
        compare_many([], [gold(raw="line-a")])


# --- failure messages ----------------------------------------------------------------

def test_failures_name_the_field_and_both_values() -> None:
    result, _ = compare(pred({**GOLD_FIELDS, "host": "web-02"}), gold())
    joined = " ".join(result.failures)
    assert "host" in joined and "web-02" in joined and "web-01" in joined


def test_failure_order_is_deterministic() -> None:
    broken = {**GOLD_FIELDS, "host": "x", "process": "y", "pid": "999"}
    first = compare(pred(broken), gold())[0].failures
    assert first == compare(pred(broken), gold())[0].failures
