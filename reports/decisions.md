# Decisions

One line of reasoning each. Ambiguities resolved in favour of keeping arms comparable.

## Phase 1 — scaffolding, contracts, config

- **Scaffolded at the repo root rather than in a `parser-lab/` subdirectory.** The repo
  *is* parser-lab; a nested directory would make `python -m src.label.arm0_zeroshot` and
  the Makefile's `cd $(REPO)` disagree about where the root is.
- **Package is literally named `src`.** The Makefile invokes `python -m src.label.$*`, so
  the import path has to be `src.*`; the project is therefore non-installable
  (`tool.uv.package = false`) and always run from the repo root.
- **Dropped the unused `field` import from the `contracts.py` snippet.** Giving
  `per_shape_f1` a `default_factory` would have made a comparison field optional, which is
  worse than a one-import deviation from the brief.
- **`make_line_id` lives in `contracts.py`** and is a 16-hex-char SHA-256 of the raw line.
  The join key is part of the contract; letting each arm hash its own way would break the
  joins the contract exists to guarantee. 16 chars keeps JSONL readable at ~1e-10 collision
  risk for corpora up to 1e6 lines.
- **JSONL read/write helpers also live in `contracts.py`.** Every arm writes the same file
  format; a second hand-rolled writer is how the format quietly forks.
- **`record_from_dict` rejects unknown keys and non-string field values.** Numeric
  `status: 200` from one arm and `"200"` from another would silently score as a mismatch,
  which reads as a model failure rather than a serialisation bug.
- **`data/gold/` and `data/unseen/` are committed; `data/raw/`, `data/generated/` and
  `runs/` are gitignored.** `CLAUDE.md` rule 13 keeps bulk data off the laptop, but the
  frozen hand-labelled sets must be identical on both machines or no number is comparable.
- **Config rejects unknown keys as well as missing ones.** "Fail loudly on a missing key"
  does not cover the more common failure: a typo'd key that loads fine and configures
  nothing.
- **`llm:` must be present in every arm config, explicitly `null` for arms with no model.**
  An absent key would be indistinguishable from a forgotten one.
- **Config paths are repo-relative in YAML and absolute in memory.** A `make` target and an
  interactive shell must resolve them the same way regardless of cwd.
- **The gateway URL is named by host (`host: spark`) and resolved from `hosts.yaml`.**
  Switching an arm to the laptop for a smoke test is then a one-word change and no URL ever
  appears in `src/`.
- **The API key is read from the environment at call time, not at load time.** `make test`
  must pass with no network and no secrets.
- **`configs/schema.json` carries a non-standard `x-required-by-shape` table.** Which fields
  are required depends on the shape, and the shape is not part of the fields object;
  unknown keywords are ignored by JSON Schema validators, so the file stays a valid schema.
- **Field values are strings throughout the schema.** The parser's job is extraction; typing
  them here would let a cast hide an extraction error.

## Phase 1 — step 3, the LLM client

- **A failed prompt comes back as a failed `Completion`, not an exception.** These are
  overnight batch jobs; losing six hours of labelling because one line upset the gateway
  is the worse failure. `BatchResult.failures` makes the loss countable.
- **A 200 with an unusable body is not retried.** A malformed response is the gateway's
  bug, not a transient one, and retrying it just burns the batch three times over.
- **Non-retryable statuses (e.g. 400) fail immediately.** A 400 means our request is
  wrong; retrying multiplies the bug instead of fixing it.
- **Backoff jitter is derived from the prompt index, not an RNG.** Retries still spread
  out, but a seeded run replays identically.
- **`Retry-After` wins over our own backoff when the server sends it.** The gateway knows
  its queue depth and we do not.
- **`BatchResult.usage_missing` is reported separately.** Token counts go straight into
  `ArmReport`; a silently-zero count would understate an arm's cost and read as a win.
- **`transport`, `api_key` and `sleeper` are injectable.** That is the seam that lets
  `make test` cover retries and concurrency with no network, no secrets and no real sleep.
- **`seed` is sent with the request when given.** Determinism is a project rule and the
  seed already has to be recorded in the report.

## Lesson layering — step 4

- **Unbuilt steps ship as red tests behind a `lesson` marker.** The specification in
  executable form beats prose: the student reads a failing assertion, not a paragraph
  describing what should fail. `pytest -m "not lesson"` stays green as the regression
  check, so "is the built code still fine" and "is this step done" are different commands.
- **Stubs raise `NotImplementedError` inside functions, never at import.** A module that
  raises on import breaks collection, and the student sees a stack trace instead of a
  test list.
- **Dataclass shapes (`FieldSchema`, `FieldDiff`) are given; the logic is not.** The data
  shape is the contract between steps 4 and 5 — letting it be invented would let step 5's
  spec drift from what step 4 produced.
- **The brief's schema-valid-but-wrong example does not work against this schema.**
  `level="GET"`, `method="INFO"` is caught, because both are enums and `GET` is not a
  level. The genuinely uncatchable swap needs two fields of the same loose type, so the
  fixture swaps syslog's `host` and `process`. Both cases are now tested: the enum catch
  is a feature, not a problem, and the contrast shows that how much a swap costs depends
  on how constrained the fields are.
- **An empty string in a required field counts as missing** in `schema.py`, but as a
  *present* field in `exact.py`. Different questions: structurally the parser has not found
  the field; for scoring, it emitted a value and got it wrong. Recorded because the two
  modules deliberately disagree.
