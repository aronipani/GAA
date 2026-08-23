# CLAUDE.md

Working agreement for this repo. Read this and `docs/INFRA.md` before writing anything.

## What this repo is

A **controlled comparison** of techniques for training small specialist parsing models.
Not a product. The deliverable is `reports/findings.md` — numbers that survive scrutiny —
backed by runnable code.

"Best parser" and "valid comparison" pull in different directions. **Comparison wins.**

## Where things run

| | Laptop (RTX 4070, 12GB, Windows) | DGX Spark (GB10, 128GB, ARM64) |
|---|---|---|
| You are here | editing, pytest, ruff, preprocessing | never — reached only via `make` |
| Runs | CPU work, eval scoring, 3B smoke tests | model serving, labelling, training |
| Holds | the repo | `data/`, `runs/`, `models/` |

---

## Hard rules

### Experiment integrity
1. **Never write to `data/gold/` or `data/unseen/`.** Hand-labelled and frozen. If a label
   looks wrong, append to `reports/gold_disputes.md` and stop.
2. **Never train on gold or unseen.** Assert it in the training entry point, not in a
   comment. (Scoring *against* gold is the harness's job — that is not training.)
3. **No LLM in the accept/reject path.** Verifier, referee and scorer are deterministic
   code. Models propose; code decides.
4. **Same base model across all arms** unless the arm is explicitly testing model size.
   Changing it silently destroys every comparison already made.
5. **Every arm emits the same `ArmReport`.** Arms reporting different fields cannot be
   compared, which defeats the purpose.

### Infrastructure
6. **GPU work goes through `make`.** Never an inline `ssh`.
7. **Remote jobs are detached and logged.** `nohup ... > runs/<ts>-<job>.log 2>&1 &`.
8. **Nothing above ~4B loads on the laptop.** 12GB. Smoke tests only.
9. **No hardcoded endpoints, model names, paths or magic numbers.** Config or constants.
10. **Assume ARM64 remotely.** Check for an aarch64 wheel before adding a dependency.
    `flash-attn` does not build; FP8 training is broken. Use SDPA and bf16. Pin versions.
11. **Batch, don't loop.** Any code that issues one request and waits is wrong for this
    hardware. Submit lists, use concurrency, write JSONL.
12. **Cap `gpu_memory_utilization` at 0.80**, and start at 0.5 per `docs/SERVING.md`.
13. **`data/raw/`, `data/generated/` and `runs/` are Spark-only** and gitignored.
    `data/gold/` and `data/unseen/` *are* committed — both machines need identical copies.
    `reports/` comes back; nothing else does.

### Scope
14. **No agent frameworks.** These loops are ~150 lines around an OpenAI-compatible
    endpoint. No CrewAI, LangGraph, AutoGen.

---

## Conventions

- Python 3.11+, `uv`, `ruff`, `pytest`. Type hints everywhere, dataclasses over dicts.
- All model access goes through the gateway. Model choice is a config value.
- JSONL only on disk, one record per line, schema documented at the top of the writing
  module.
- Fixed seeds, `temperature=0` unless the arm needs sampling. Record the seed in the report.
- Every module opens with a two-line docstring: what it does, what it deliberately doesn't.
- Comments explain *why*, never *what*.
- Small functions with obvious names. No base classes with one subclass, no registries.
- Tests use hand-built fixtures, never generated data. Include the nasty cases.

## Testing priorities, in order

1. **The verifier.** A silently broken grader turns every downstream number into noise, and
   it is the failure you are least likely to notice.
2. **The eval scorer.** Missing fields, extra fields, empty vs absent — enough edge cases to
   get quietly wrong.
3. **The regression corpus ratchet** (Phase 2).
4. Arm implementations. A broken arm produces an obviously bad number; a broken verifier
   produces a plausible one.

## When you're unsure

State the ambiguity, pick the option that keeps arms comparable, and note it in
`reports/decisions.md` with one line of reasoning. Don't decide silently — a choice made
quietly in arm 4 that differs from arm 3 is exactly how comparisons become invalid.
