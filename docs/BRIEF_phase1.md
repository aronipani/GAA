# Claude Code — Phase 1 setup prompt

Copy everything below the line into Claude Code in an empty repo.

---

You're scaffolding a research repo called `parser-lab`. Read this whole brief before
writing any code. Build only what Phase 1 asks for — do not get ahead.

## What we're building

A controlled comparison of techniques for training a small model to parse logs. Several
"arms" (different labelling techniques) feed the same training and evaluation pipeline, so
we can measure which technique is worth its compute.

The deliverable is a report with defensible numbers, not a product. When "make it better"
and "keep the comparison valid" conflict, comparison wins.

## Where things run

| | Laptop (RTX 4070, 12GB, Windows) | DGX Spark (128GB, ARM64, remote) |
|---|---|---|
| Used for | editing, tests, lint, small CPU jobs | serving models, labelling, training |
| Access | you are here | only via `make` targets over SSH |

Everything that needs a GPU goes through a `make` target that runs detached and logs to a
file. Never an inline `ssh`. Never a foreground training run.

## Phase 1 scope

Build these, in this order:

1. Repo skeleton + `pyproject.toml` (uv, ruff, pytest)
2. Config loading
3. LLM client — OpenAI-compatible, batched, async
4. Verifier + tests
5. Eval harness + tests
6. Preprocessing: Drain3 clustering, shape classification, multiline joining
7. Arm 0 (zero-shot baseline) and Arm 1 (classical regex baseline)
8. `Makefile`

**Do not build** the debate loop, the teacher distillation arm, or any training code yet.
Those come in Phase 2, once the verifier and eval harness are trustworthy.

## Contracts — get these exactly right

Everything else depends on these. Put them in `src/contracts.py` and import from there;
do not redefine them locally.

```python
from dataclasses import dataclass, field
from typing import Protocol

Shape = str  # "syslog" | "json_lines" | "logfmt" | "apache_clf" | "multiline_trace" | "unknown"

@dataclass
class LogRecord:
    """One log line and its parsed fields. The unit of everything."""
    raw: str
    fields: dict[str, str]
    shape: Shape
    source: str            # which arm or human produced this
    line_id: str           # stable hash of raw; used for joins and dedup

@dataclass
class VerifyResult:
    passed: bool
    failures: list[str]    # human-readable, one per failed check. empty iff passed.

@dataclass
class ArmReport:
    """Every arm emits this. Same fields, always — arms that differ can't be compared."""
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
```

JSONL is the only on-disk format. One `LogRecord` per line. Document the schema at the top
of any module that writes a file.

## Repo layout

```
parser-lab/
├── CLAUDE.md
├── Makefile
├── pyproject.toml
├── configs/
│   ├── hosts.yaml           # spark host, gateway URLs
│   ├── schema.json          # JSON Schema for parsed log fields
│   └── arm0.yaml, arm1.yaml
├── data/
│   ├── raw/                 # gitignored
│   ├── gold/                # HAND-LABELLED. code never writes here.
│   ├── unseen/              # held-out whole shapes
│   └── generated/           # gitignored
├── reports/                 # arm_XX.json + markdown write-ups
├── runs/                    # gitignored; remote job logs
├── src/
│   ├── contracts.py
│   ├── config.py
│   ├── llm.py
│   ├── preprocess/
│   │   ├── cluster.py       # drain3
│   │   ├── multiline.py     # continuation-line joining
│   │   └── shape.py         # cluster -> Shape
│   ├── verify/
│   │   ├── schema.py        # JSON Schema, types, formats
│   │   └── exact.py         # exact field match vs gold
│   ├── label/
│   │   ├── arm0_zeroshot.py
│   │   └── arm1_classical.py
│   └── eval/
│       └── harness.py
└── tests/
```

## Module notes

**`src/config.py`** — load YAML into typed objects. Fail loudly on a missing key; never
default silently. A wrong config that runs is worse than one that crashes.

**`src/llm.py`** — one async client against an OpenAI-compatible endpoint. Requirements:
- Takes a *list* of prompts and runs them concurrently, bounded by a semaphore. There is no
  single-prompt path. The remote box is bandwidth-bound and only pays off under concurrency.
- Returns text plus token counts — the report needs them.
- Retries with backoff on 429 and 5xx.
- Base URL and model name come from config. **No hardcoded endpoints or model names
  anywhere in the codebase.**

**`src/preprocess/multiline.py`** — join continuation lines (leading whitespace, or no
timestamp at line start) before anything else runs. Stack traces and pretty-printed JSON
aren't line-oriented; if this doesn't run first, everything downstream fights it forever.

**`src/verify/`** — this is the quality signal for the entire project, so it gets the most
care and the best tests. `schema.py` checks structure: valid JSON, required fields present,
correct types and formats. `exact.py` checks correctness against gold: exact field-value
match. Keep them separate — schema-valid-but-wrong is the interesting failure mode and we
need to see it distinctly.

**`src/eval/harness.py`** — loads gold, runs an arm, scores it, writes
`reports/<arm>.json` as an `ArmReport`. Field-level P/R/F1 plus exact match. Score
`data/unseen/` separately into `unseen_shape_f1`. The scorer is frozen once written —
changing it invalidates every earlier arm's numbers.

**`src/label/arm1_classical.py`** — Drain3 templates converted to named-capture regexes by
hand. No LLM. This is the baseline every LLM arm must beat, and it's often closer than
people expect.

## Makefile

```makefile
SPARK := aroni@spark.lan
REPO  := /srv/parser-lab
RUN   := $(shell date +%Y%m%d-%H%M%S)

test:                 # local, fast, no GPU
	pytest -q && ruff check .

sync:
	git push && ssh $(SPARK) 'cd $(REPO) && git pull --ff-only'

label-%: sync         # make label-arm0 -> detached remote job
	ssh $(SPARK) 'cd $(REPO) && nohup python -m src.label.$* \
	  --config configs/$*.yaml > runs/$(RUN)-$*.log 2>&1 & echo $$!'

logs:
	ssh $(SPARK) 'tail -f $$(ls -t $(REPO)/runs/*.log | head -1)'

gpu:
	ssh $(SPARK) 'nvidia-smi --query-gpu=memory.used,memory.total --format=csv'

pull-reports:
	scp -r $(SPARK):$(REPO)/reports/* ./reports/
```

## Rules

1. **Never write to `data/gold/` or `data/unseen/`.** Hand-labelled, frozen. If a label
   looks wrong, append to `reports/gold_disputes.md` and stop.
2. **Never train or evaluate on gold.** Assert it in code, not in a comment.
3. **No LLM judges anything.** Verifier and scorer are deterministic code. Models propose;
   code decides.
4. **No hardcoded endpoints, model names, paths, or magic numbers.** Config or constants.
5. **Batch, don't loop.** Any code path that sends one request and waits is wrong here.
6. **ARM64 remote.** Check for an aarch64 wheel before adding a dependency. `flash-attn`
   won't build. Pin versions.
7. **No agent frameworks.** These are ~150-line loops around an HTTP endpoint. No CrewAI,
   LangGraph, or AutoGen.
8. **`data/`, `runs/`, `generated/` are gitignored.** Only `reports/` comes back from the
   Spark.

## Style

- Python 3.11+, type hints everywhere, dataclasses over dicts.
- Small functions with obvious names. No clever abstractions, no base classes with one
  subclass, no plugin registries. This code will be read more than it is run.
- Every module opens with a two-line docstring: what it does, and what it deliberately
  doesn't do.
- Tests use hand-built fixtures, not generated data. Include the nasty cases: embedded
  JSON with escaped quotes, unicode, truncated lines, a stack trace, a line where the
  fields are correctly typed but semantically swapped (`level="GET"`, `method="INFO"`).
- Comments explain *why*, never *what*.

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
□ CLAUDE.md written, containing the Rules section above
```

## Working style

Go module by module in the Phase 1 order. After each one, run the tests and tell me what
you built and what you chose. If a requirement here is ambiguous, say so and pick the
option that keeps arms comparable — then note the choice in `reports/decisions.md` with
one line of reasoning. Don't silently decide.

Start with `contracts.py`, `config.py`, and the tests directory. Show me those before
continuing.
