# parser-lab

A controlled comparison of techniques for training a small model to parse logs. Several
"arms" (labelling techniques) feed the same evaluation pipeline, so we can measure which
technique is worth its compute.

The deliverable is a report with defensible numbers, not a product. When "make it better"
and "keep the comparison valid" conflict, **comparison wins**.

---

## Start here

```bash
uv sync
uv run pytest            # 68 tests, ~0.3s, no network, no GPU
uv run ruff check .
```

Then read, in order:

| | |
|---|---|
| `CLAUDE.md` | the rules. Non-negotiable. |
| `LLD.md` | per-file pseudocode spec — what to write, and the tests it must pass |
| `PIPELINE.md` | how the pieces connect, and where the design docs contradict each other |
| `src/contracts.py` | the shared types. Everything imports from here. |
| `EXPLAINER.md` | plain-English version, if the above assumes too much |

Background, vendored so the repo is self-contained: `docs/PROJECT.md` (arms, milestones,
metrics), `docs/ARCHITECTURE.md`, `docs/TECHNIQUES.md` (why each arm exists),
`docs/INFRA.md` and `docs/SERVING.md` (the two machines), `docs/BRIEF_phase1.md` (the
original build brief).

Where those conflict: **`docs/SERVING.md` > `docs/INFRA.md` > `docs/PROJECT.md`** on
hardware and serving; `docs/BRIEF_phase1.md` wins on code contracts. Seven specific
conflicts are catalogued at the bottom of `PIPELINE.md`.

---

## Build order

Phase 1. Each step is done when its gate passes. Don't start a step before the one above it.

| # | Build | Gate | State |
|---|---|---|---|
| 1 | Repo skeleton, `pyproject.toml` | `uv sync`, ruff and pytest clean | done |
| 2 | `src/config.py` | missing / unknown / null / wrong-type keys all raise | done |
| 3 | `src/llm.py` | batched, retries, concurrency — all tested offline | done |
| 4 | `src/verify/schema.py`, `src/verify/exact.py` | valid, schema-invalid, **and schema-valid-but-wrong** all covered | next |
| 5 | `src/eval/harness.py` | 5-record toy gold scored against hand-computed literals | |
| 6 | `src/preprocess/{multiline,cluster,shape}.py` | 6-line Java stack trace joins into 1 record | |
| 7 | `src/label/arm1_classical.py`, `src/label/arm0_zeroshot.py` | both emit `reports/armN.json`; arm1 needs no network | |
| 8 | `Makefile` | `make label-arm1` produces a detached job and a log file | |

**Not in Phase 1:** the debate loop, teacher distillation, or any training code. Those come
once the verifier and eval harness are trustworthy.

`LLD.md` has the pseudocode, invariants and required tests for every file above.

---

## Definition of done

```
□ uv sync works; ruff and pytest both clean
□ make test passes on the laptop with no GPU and no network
□ src/verify has tests covering: valid, schema-invalid, and schema-valid-but-wrong
□ src/eval/harness.py scores a 5-record toy gold set by hand-checkable numbers
□ multiline.py correctly joins a 6-line Java stack trace into one record
□ arm1_classical runs end to end on 100 sample lines and emits reports/arm1.json
□ arm0_zeroshot runs against the configured endpoint and emits reports/arm0.json
□ Makefile targets exist; label-% produces a detached job and a log file
□ CLAUDE.md written, containing the Rules section
```

Steps 1–3 and `CLAUDE.md` are done. The rest is open.

---

## Before you can run an arm

The pipeline needs a hand-made answer key, and no code may produce it:

1. Collect 50–200K raw lines across as many shapes as you can reach. Anonymise on ingest.
2. Run Drain3 (step 6) to map the shape distribution.
3. Hand-label 300 stratified lines into `data/gold/logs_gold.jsonl`, over-sampling weird
   ones. Keep every judgement call in `data/gold/LABELLING_NOTES.md` — you will disagree
   with yourself by line 200.
4. Hold out 2–3 whole shapes into `data/unseen/unseen.jsonl`.

If the gold labels come from a model, every number measures *agreement with that model*,
not accuracy, and a specialist can never appear to beat its own teacher. One tedious day is
the price of admission for every conclusion that follows.

---

## Open decisions

- **Base model unsettled.** `Qwen2.5-3B-Instruct` (`docs/PROJECT.md`, `CLAUDE.md`) vs
  Qwen3.5-4B/2B (`docs/SERVING.md`). `configs/arm0.yaml` says the former. Fix it before
  arm 0 produces a number — rule 4 makes this unfixable afterwards.
- **`ArmReport` has no `base_model` field**, so a report doesn't record which model made it.
- **`level` enum** in `configs/schema.json` is uppercase-only and missing the syslog
  severities (`emerg`, `alert`, `crit`, `err`). Cheap to widen now, expensive after gold
  labelling.
- **`llm.LLMError`** is declared and never raised. Raise it or delete it.

## Layout

```
CLAUDE.md  LLD.md  PIPELINE.md  EXPLAINER.md  README.md
configs/   hosts.yaml  schema.json  arm0.yaml  arm1.yaml
data/      raw/ gold/ unseen/ generated/     # gold + unseen committed, rest gitignored
docs/      vendored design documents
reports/   arm_XX.json, decisions.md, findings.md
runs/      remote job logs (gitignored)
src/       contracts.py config.py llm.py preprocess/ verify/ label/ eval/
tests/
```
