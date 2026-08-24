# Step 4 — the verifier

> The most important code in the project. A broken arm produces an obviously bad number.
> A broken verifier produces a **plausible** one, and every downstream conclusion inherits
> it. This is why it comes before any arm runs.

**Build:** `src/verify/schema.py`, `src/verify/exact.py`
**Gate:** `uv run pytest -m lesson` green — 49 tests
**Time:** a few hours

---

## 0 · Constraints

`CLAUDE.md`, rule 3: no LLM in the accept/reject path. Both files are deterministic code.
Rule 1: neither may write to `data/gold/`. `exact.py` *reads* gold — that is its job.

## 1 · Read first

| Read | Because |
|---|---|
| `src/contracts.py` → `record_from_dict` | the same discipline on a smaller problem: collect every failure, name the field, never coerce |
| `src/config.py` → `reject_unknown` | why a closed vocabulary beats an open one, and what the error message should say |
| `configs/schema.json` | the field vocabulary you are enforcing, and the `x-required-by-shape` table |
| `tests/fixtures.py` | the records you will be graded on, including the nasty ones |

Fifteen minutes. Do not skip `record_from_dict` — `verify_schema` is the same shape of
function and the error-message style should match.

## 2 · What you are building

Two modules that answer two different questions about the same record:

```
schema.py :  is this WELL FORMED?    known fields, right types, required ones present
exact.py  :  is this RIGHT?          every value identical to the human's answer
```

They stay separate because a record can pass the first and fail the second, and that gap
is the single most interesting thing the report shows. Merge them and
`schema_compliance` and `field_f1` stop measuring different things.

### `schema.py`

```
load_field_schema(path) -> FieldSchema
    parse configs/schema.json
    pull out x-required-by-shape, dropping keys starting "_"
    if any remaining key is not in contracts.SHAPES -> SchemaConfigError
    return FieldSchema(Draft202012Validator(doc), required_by_shape)

verify_schema(record, schema) -> VerifyResult
    failures = []
    if record.shape not in SHAPES              -> failure naming the shape
    for each jsonschema error over record.fields -> failure naming the field
        (this covers type, enum, pattern, minLength, additionalProperties)
    for each field required by record.shape
        if absent or ""                        -> failure naming the field
    sort failures                              # deterministic, so two runs compare
    return VerifyResult(passed = not failures, failures)
```

Three things the tests will hold you to:

- **Every failure, not the first.** Three bad fields produce three messages.
- **Deterministic order.** `iter_errors` does not guarantee one; sort.
- **Empty string counts as missing** for a required field. A parser that emits `""` for a
  field it could not find has still not found it.

### `exact.py`

```
compare(predicted, gold) -> (VerifyResult, FieldDiff)
    if predicted.line_id != gold.line_id -> ContractError    # a join bug, not a miss
    for name in union of both field sets:
        both, equal      -> tp += 1
        both, different  -> fp += 1 AND fn += 1, record (name, got, want)
        prediction only  -> fp += 1, record extra
        gold only        -> fn += 1, record missing
    passed = (fp == 0 and fn == 0)
```

**The one decision that matters: a wrong value costs both an FP and an FN.** It is one
hallucinated field plus one missed field. Charge it once and precision flatters every arm
by the same amount, which is worse than a visible error — it is an invisible one.

`compare_many` pairs by `line_id`, never by position, and returns results in gold order.
An unpaired record on either side raises: dropping a gold record silently shrinks the
denominator and inflates every score.

## 3 · The assignment

```bash
uv run pytest -m lesson          # 49 red
```

Work in this order — the fixture depends on `load_field_schema`, so 29 of them are
collection errors until it exists:

1. `load_field_schema` → the errors become failures
2. `verify_schema` happy path → the per-shape valid records pass
3. `verify_schema` failure cases → patterns, enums, unknown fields, required fields
4. `compare` tallies
5. `compare_many` pairing

The built suite must stay green throughout:

```bash
uv run pytest -m "not lesson"    # 68, always
```

## 4 · The two tests to read before you write anything

`test_schema_cannot_catch_a_semantically_swapped_record` and
`test_the_semantic_swap_that_schema_validation_passes` are the same record. It must
**pass** the first and **fail** the second.

A syslog line where `host` and `process` are swapped is perfectly well formed — both are
free-form non-empty strings — and completely wrong. If you make `schema.py` clever enough
to catch it, you have deleted the distinction the whole report is built on.

> **Note on the canonical example.** `docs/BRIEF_phase1.md` uses `level="GET"`,
> `method="INFO"` as the schema-valid-but-wrong case. Against *this* schema it is not
> schema-valid, because `level` and `method` are both enums and `GET` is not a level —
> `test_an_enum_does_catch_a_swap_between_enum_fields` pins that down. How much a swap
> costs depends on how constrained the fields are, which is itself an argument for
> constraining them. The genuinely uncatchable swap needs two fields of the same loose
> type, hence host/process.

## 5 · Gate

```
□ uv run pytest         → 117 passed, 0 failed
□ uv run ruff check .   → clean
□ every module opens with its two-line docstring, and you deleted the STUB line
```

## 6 · Write down what you decided

Append to `reports/decisions.md`, one line of reasoning each. At minimum:

- how you ordered the failure list, and why that order
- whether an unknown field is one failure or one per field
- what `compare_many` does when both sides are empty

If you departed from anything above, that is fine — record it. A choice made quietly in
step 4 that contradicts step 5 is exactly how the comparison becomes invalid.

---

**Next:** step 5, the eval harness. It consumes `FieldDiff` from this step and freezes the
scoring for every arm that follows, so nothing here can change afterwards.
