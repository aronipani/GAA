# Low-level design

One section per file. `BUILT` sections document what exists; `TO BUILD` sections are the
spec to implement against. Pseudocode is Python-flavoured, not literal.

Conventions used throughout: all paths absolute, resolved via `src.config`; all on-disk
files JSONL; no module hardcodes an endpoint, model name, or path.

---

## `src/contracts.py` — BUILT

**Purpose.** Shared types + their JSONL encoding. Everything imports from here.

```
CONST SHAPES = (syslog, json_lines, logfmt, apache_clf, multiline_trace, unknown)
CONST UNKNOWN_SHAPE = "unknown"
CONST LINE_ID_HEX_CHARS = 16

make_line_id(raw) -> str:
    return sha256(utf8(raw)).hexdigest()[:16]

is_valid_shape(s) -> bool: return s in SHAPES

DATACLASS LogRecord   { raw, fields: dict[str,str], shape, source, line_id }
DATACLASS VerifyResult{ passed: bool, failures: list[str] }
DATACLASS ArmReport   { arm, field_precision, field_recall, field_f1, exact_match,
                        schema_compliance, per_shape_f1, unseen_shape_f1,
                        label_tokens_in, label_tokens_out, label_wall_clock_s,
                        seed, notes="" }
PROTOCOL  Arm         { name; label(lines) -> list[LogRecord] }

new_record(raw, fields, shape, source) -> LogRecord:
    return LogRecord(raw, copy(fields), shape, source, make_line_id(raw))

record_from_dict(payload) -> LogRecord:              # strict; raises ContractError
    require keys == {raw, fields, shape, source, line_id}   # missing AND extra are errors
    require isinstance(raw, str)
    require isinstance(fields, dict) and all keys/values are str
    require shape, source, line_id are str
    return LogRecord(...)

write_jsonl(path, records):  mkdir parents; one json.dumps per line; ensure_ascii=False
read_jsonl(path) -> list:    skip blank lines; on error raise ContractError("path:lineno ...")
write_report(path, report):  json.dumps(asdict, indent=2)
```

**Invariants.** `line_id == make_line_id(raw)` for anything built via `new_record`.
`fields` is `str -> str`, never numeric, never null. Round-trip is lossless.

**Deliberately absent.** `record_from_dict` does *not* check `shape in SHAPES` — only that
it is a string. Shape validity is `verify/schema.py`'s job.

---

## `src/config.py` — BUILT

**Purpose.** YAML into frozen dataclasses. Every anomaly raises `ConfigError`.

```
CONST REPO_ROOT, CONFIGS_DIR, DATA_DIR, GOLD_DIR, UNSEEN_DIR,
      GENERATED_DIR, RAW_DIR, REPORTS_DIR, RUNS_DIR
CONST KNOWN_HOSTS = ("spark", "laptop")

# strict primitives, exported so arm modules parse `params` just as strictly
require_str/int/float/path(mapping, key, where) -> T:
    if key absent          -> ConfigError "missing required key"
    if value is None       -> ConfigError "is null"
    if wrong type          -> ConfigError "expected X"       # bool is NOT an int
    if str and blank       -> ConfigError "is empty"
    if path and relative   -> REPO_ROOT / value              # cwd never matters

reject_unknown(mapping, known, where):
    extra = mapping.keys() - known
    if extra -> ConfigError "unknown key(s)"                 # a typo must not load

load_hosts(path=HOSTS_CONFIG_PATH) -> HostsConfig:
    raw = yaml.safe_load; reject_unknown(raw, KNOWN_HOSTS)
    spark  = SparkHost(host, user, repo, gateway)            # all required
    laptop = LaptopHost(gateway)
    HostsConfig.gateway_for(name) -> spark|laptop gateway, else ConfigError

load_arm_config(path, hosts=None) -> ArmConfig:
    reject_unknown(raw, _ARM_KEYS)
    every key in _ARM_KEYS must be PRESENT                   # incl. `llm`
    if raw.llm is None: llm = None                           # explicit "no model"
    else:
        reject_unknown(llm_raw, _LLM_KEYS)
        base_url = hosts.gateway_for(llm_raw.host)           # never a literal URL
        require max_concurrency >= 1, max_retries >= 0
    params = raw.params or {}                                # arm parses these itself
    return ArmConfig(arm, seed, input/output/report/schema paths, llm, params)

resolve_api_key(llm) -> str:                                 # read at CALL time
    key = os.environ[llm.api_key_env] or ConfigError
```

**Invariant.** A config that loads is fully specified. There is no default value anywhere.

---

## `src/llm.py` — BUILT

**Purpose.** One async batched client for an OpenAI-compatible chat endpoint.

```
CONST CHAT_COMPLETIONS_PATH = "/chat/completions"
CONST RETRYABLE_STATUS = {408,409,429,500,502,503,504}
CONST JITTER_FRACTION = 0.1, MAX_BACKOFF_S = 60.0

DATACLASS Completion { index, text, tokens_in, tokens_out, error=None; .ok }
DATACLASS BatchResult{ completions, tokens_in, tokens_out, wall_clock_s,
                       usage_missing=0, attempts=0; .failures, .texts }

_backoff_seconds(attempt, base, index):
    return min(base * 2**attempt, 60) * (1 + 0.1 * (index % 10)/10)   # no RNG: replayable

_retry_after_seconds(response): float(header) if parseable else None

CLASS LLMClient(config, api_key=None, transport=None, sleeper=None):
    # the three optional args are the test seam: no network, no secret, no real sleep
    semaphore = Semaphore(config.max_concurrency)
    httpx.AsyncClient(base_url=config.base_url, timeout, Bearer auth)

    async complete_many(prompts, system=None, seed=None, response_format=None):
        if prompts empty: return empty BatchResult      # zero requests
        t0 = monotonic()
        results = await gather(_complete_one(i, p, ...) for i,p in enumerate(prompts))
        return BatchResult(results in input order, summed tokens, elapsed,
                           usage_missing = count(ok and tokens_in == 0))

    async _complete_one(index, prompt, ...):
        body = {model, messages[(system?),user], temperature, max_tokens,
                seed?, response_format?}               # all from config
        async with semaphore:
            for attempt in 0..max_retries:
                try: r = await post(CHAT_COMPLETIONS_PATH, body)
                except HTTPError: wait = backoff; continue
                if r.status == 200:
                    try:    return parse(r.json())
                    except: return Completion(error="bad response")   # NOT retried
                if r.status not in RETRYABLE_STATUS:
                    return Completion(error=f"HTTP {status}")         # NOT retried
                wait = retry_after(r) or backoff
                if attempt < max_retries: await sleeper(wait)
        return Completion(error="... after N attempts")   # never raises
```

**Invariants.** Results in input order. Peak in-flight == `max_concurrency` under load.
One prompt's failure never aborts the batch.

**Known defect.** `LLMError` is declared and never raised. Either raise it from
`complete_many` when the batch cannot be attempted, or delete it.

---

## `src/preprocess/multiline.py` — TO BUILD (step 6, but runs first at runtime)

**Purpose.** Join continuation lines into logical records before anything else sees them.

```
CONST MAX_JOINED_LINES = 200      # a pathological file must not build one huge record
CONST RECORD_START = [ ISO8601, "MMM DD HH:MM:SS", "[DD/Mon/YYYY:...]",
                       "{" , epoch-millis, "level=" ]     # ordered regexes

starts_new_record(line) -> bool:
    if line == "": return False
    if line[0] in " \t": return False           # indented -> continuation
    return any(p.match(line) for p in RECORD_START)

join(lines: list[str]) -> list[str]:
    out, buf = [], []
    for line in lines:
        if buf == [] or (starts_new_record(line) and len(buf) < MAX_JOINED_LINES):
            if buf: out.append("\n".join(buf))
            buf = [line]
        else:
            buf.append(line)
            if len(buf) == MAX_JOINED_LINES:    # forced flush, do not grow unbounded
                out.append("\n".join(buf)); buf = []
    if buf: out.append("\n".join(buf))
    return out
```

**Invariants.** First line always starts a record, whatever it looks like. Output preserves
input order. `"\n".join(output) == "\n".join(input)` — joining loses nothing.

**Tests.** 6-line Java stack trace → exactly 1 record. Pretty-printed JSON → 1 record.
Plain syslog lines → n records, unchanged. Truncated final line survives. Leading blank
lines don't crash. A 500-line trace flushes at the cap rather than growing.

---

## `src/preprocess/cluster.py` — TO BUILD (step 6)

**Purpose.** Drain3 over logical lines → templates. No GPU, no model.

```
DATACLASS Cluster { template_id: str, template: str, size: int, examples: list[str] }

CONST DRAIN_SIM_TH, DRAIN_DEPTH, MAX_EXAMPLES_PER_CLUSTER   # or from config

cluster(lines: list[str], sim_th, depth) -> (list[Cluster], dict[line_id -> template_id]):
    miner = TemplateMiner(config with persistence DISABLED)   # no hidden state between runs
    for line in lines:                     # file order, fixed: Drain3 is order-dependent
        r = miner.add_log_message(first_physical_line(line))  # cluster on the header only
        assign line_id -> r.cluster_id
    build Clusters from miner.drain.clusters, capped examples
    return sorted by size desc
```

**Decisions to record.** Cluster on the *first physical line* of a joined record — a stack
trace body would otherwise produce one cluster per unique frame. Persistence off, so a run
is reproducible from the corpus alone.

**Tests.** Same input twice → identical template ids. Two syslog lines differing only in
pid → one cluster. Reordering input is allowed to change ids (assert only that it is
documented, not that it is stable).

---

## `src/preprocess/shape.py` — TO BUILD (step 6)

**Purpose.** One logical line → a `Shape`. Deterministic, ordered, first match wins.

```
classify(raw: str) -> Shape:
    if "\n" in raw and TRACE_FRAME.search(raw):   return "multiline_trace"
    if raw.lstrip().startswith("{") and json_parses(raw): return "json_lines"
    if APACHE_CLF.match(raw):                     return "apache_clf"
    if SYSLOG.match(raw):                         return "syslog"
    if LOGFMT_PAIR.search(raw) and pair_density(raw) >= LOGFMT_MIN_PAIRS: return "logfmt"
    return UNKNOWN_SHAPE
```

**Order matters and is the design.** `multiline_trace` first because a joined record may
also start with a timestamp. `apache_clf` before `syslog` because CLF also has a bracketed
timestamp. `logfmt` last because `key=value` appears inside other formats.

**Tests.** One case per shape from `tests/fixtures.py`, plus: a syslog line whose message
contains `key=value` classifies as `syslog`, not `logfmt`; a truncated JSON line falls to
`unknown` rather than raising.

---

## `src/verify/schema.py` — TO BUILD (step 4, next)

**Purpose.** Is this record *well formed*? Structure only. Never touches gold.

```
DATACLASS FieldSchema { validator: jsonschema.Validator,
                        required_by_shape: dict[Shape, list[str]] }

load_field_schema(path) -> FieldSchema:
    doc = json.load(path)
    required = doc["x-required-by-shape"] minus "_comment" keys
    require every key of `required` is in SHAPES     # schema and code must agree
    return FieldSchema(Draft202012Validator(doc), required)

verify_schema(record: LogRecord, schema: FieldSchema) -> VerifyResult:
    failures = []
    if not is_valid_shape(record.shape):
        failures += [f"unknown shape '{record.shape}'"]
    for err in sorted(schema.validator.iter_errors(record.fields), key=path):
        failures += [f"{join(err.path)}: {err.message}"]   # covers type, enum,
                                                           # pattern, additionalProperties
    for name in schema.required_by_shape.get(record.shape, []):
        if name not in record.fields or record.fields[name] == "":
            failures += [f"missing required field '{name}' for shape {record.shape}"]
    return VerifyResult(passed=not failures, failures=failures)

verify_many(records, schema) -> list[VerifyResult]
```

**Invariants.** `passed` iff `failures == []`. Failure order is deterministic (sorted), so
diffs between runs are meaningful. Every failure names the field.

**Tests — the three the brief requires, plus edges.**
- valid record for each shape → `passed`
- schema-invalid: `status="20"` (pattern), `level="LOUD"` (enum), `trace_id=...`
  (additionalProperties), missing required field, empty-string required field
- **schema-valid-but-wrong**: `level="GET"`, `method="INFO"` → `passed is True`.
  This is the point of the split; assert it explicitly.
- unicode and escaped-quote values pass structural checks
- unknown `shape` string → one failure, no exception

---

## `src/verify/exact.py` — TO BUILD (step 4)

**Purpose.** Is this record *right*? Exact field-value match against gold.

```
DATACLASS FieldDiff { tp: int, fp: int, fn: int,
                      wrong: list[(name, got, want)],
                      missing: list[name], extra: list[name] }

compare(predicted: LogRecord, gold: LogRecord) -> (VerifyResult, FieldDiff):
    require predicted.line_id == gold.line_id  else ContractError   # a join bug, not a miss
    for name in union(pred.fields, gold.fields):
        in both and equal      -> tp += 1
        in both and different  -> fp += 1; fn += 1; wrong += [(name, got, want)]
        pred only              -> fp += 1; extra   += [name]
        gold only              -> fn += 1; missing += [name]
    failures = sorted human-readable lines, one per wrong/missing/extra
    return VerifyResult(passed = (fp == 0 and fn == 0), failures), diff

compare_many(predicted, gold) -> list[(VerifyResult, FieldDiff)]:
    index both by line_id; a prediction with no gold counterpart is an error, not a skip
```

**Decisions to record.** A wrong value costs both an FP and an FN — it is one hallucinated
field plus one missed field, and counting it once would flatter precision. Empty string is
a *present* field, not an absent one; `contracts.py` already forbids null.

**Tests.** Perfect match → `tp=n, fp=0, fn=0`. The semantic swap → `tp=0, fp=2, fn=2` and
`passed is False` (contrast with `schema.py` passing the same record). Missing field.
Extra field. Value differing only by trailing whitespace → not equal. Mismatched
`line_id` → raises.

---

## `src/eval/harness.py` — TO BUILD (step 5). FROZEN ONCE WRITTEN.

**Purpose.** Load gold, run an arm, score it, write `reports/<arm>.json`.

```
CONST SCORER_VERSION = 1        # bump = every earlier report is invalid; do not bump

micro_prf(diffs) -> (p, r, f1):
    TP, FP, FN = sums over diffs
    p  = TP/(TP+FP) if TP+FP else 0.0
    r  = TP/(TP+FN) if TP+FN else 0.0
    f1 = 2pr/(p+r)  if p+r    else 0.0

score(predicted, gold, schema) -> dict:
    diffs   = [compare(p, g).diff for p,g paired by line_id]
    p,r,f1  = micro_prf(diffs)
    exact   = mean(pred.fields == gold.fields)
    compl   = mean(verify_schema(pred).passed)
    by_shape= { shape: micro_prf(diffs of that shape).f1 for shape in shapes present }

run(arm: Arm, cfg: ArmConfig) -> ArmReport:
    gold   = read_jsonl(GOLD_DIR/"logs_gold.jsonl")
    unseen = read_jsonl(UNSEEN_DIR/"unseen.jsonl")
    assert gold and unseen line_id sets are disjoint
    assert no path under GOLD_DIR or UNSEEN_DIR is writable by this run
    t0 = monotonic()
    pred        = arm.label([g.raw for g in gold])       # raw lines ONLY, never fields
    pred_unseen = arm.label([u.raw for u in unseen])
    elapsed = monotonic() - t0
    main   = score(pred, gold, schema)
    unseen_f1 = score(pred_unseen, unseen, schema).f1
    write_report(cfg.report_path, ArmReport(..., seed=cfg.seed))
```

**Invariants.**
- The arm receives `raw` strings and nothing else. Never `gold.fields`.
- Micro-averaged, not macro: every field counts once, so a shape with 40 fields weighs more
  than one with 4. Macro would let a rare shape swing the headline number.
- Empty denominators score 0.0, never NaN — NaN propagates silently into a report.
- Gold is read-only: assert, don't comment.

**Tests.** A 5-record toy gold set with hand-computed P/R/F1 and exact-match, checked
against literals in the test — not against a re-implementation of the formula. Plus: an arm
returning nothing scores 0.0 and does not divide by zero; an arm returning extra records
raises; `per_shape_f1` keys exactly match shapes present in gold.

---

## `src/label/arm1_classical.py` — TO BUILD (step 7)

**Purpose.** Drain3 templates hand-converted to named-capture regexes. No LLM. The baseline
every LLM arm must beat.

```
load_patterns(path) -> dict[Shape, list[compiled]]:
    yaml: shape -> [ {name, regex} ]
    re.compile each at load; a bad regex is a startup error, not a runtime one

CLASS ClassicalArm(patterns):
    name = "arm1_classical"
    label(lines) -> list[LogRecord]:
        for raw in lines:
            shape = classify(raw)
            for pattern in patterns.get(shape, []) + patterns[UNKNOWN_SHAPE]:
                m = pattern.match(raw)
                if m: return new_record(raw, m.groupdict() minus Nones, shape, name)
            return new_record(raw, {}, shape, name)     # a miss is {} , never a crash

main(--config): cfg = load_arm_config; arm = ClassicalArm(...);
                harness.run(arm, cfg)                    # writes reports/arm1.json
```

**Tests.** Runs end to end on 100 sample lines and emits a report. An unmatched line yields
an empty-field record. First matching pattern wins, deterministically.

---

## `src/label/arm0_zeroshot.py` — TO BUILD (step 7)

**Purpose.** Base model, zero-shot. Batched. The "what do I get for free" number.

```
build_prompt(raw, shape, schema_field_names) -> str:      # template from params.prompt_path
    "Extract fields as JSON. Allowed keys: {names}. Line: {raw}"

parse_response(text) -> dict[str,str]:
    strip code fences; json.loads
    coerce every value to str      # models emit status: 200; contracts forbid non-str
    drop non-scalar values
    on failure return {}           # a miss, scored as such, not an exception

CLASS ZeroShotArm(client, cfg):
    name = "arm0_zeroshot"
    label(lines):
        prompts = [build_prompt(...) for each]            # ONE batch, not a loop
        result  = await client.complete_many(prompts, system=..., seed=cfg.seed,
                                             response_format={"type":"json_object"})
        records = [new_record(raw, parse_response(c.text) if c.ok else {}, shape, name)
                   for raw, c in zip(lines, result.completions)]
        carry result.tokens_in/out/wall_clock_s into the report
```

**Invariants.** Exactly one `complete_many` call per `label()`. A failed completion becomes
an empty-field record so the arm scores honestly rather than crashing.

**Tests.** Against a mock transport: 100 lines → 1 batch, 100 records, order preserved.
Fenced JSON parses. Numeric values coerce to strings. Garbage response → `{}`.

---

## `Makefile` — TO BUILD (step 8)

```
SPARK := $(shell yq .spark.user configs/hosts.yaml)@$(shell yq .spark.host ...)  # or literal
REPO  := /srv/parser-lab
RUN   := $(shell date +%Y%m%d-%H%M%S)

test:          pytest -q && ruff check .            # local, no GPU, no network
sync:          git push && ssh $(SPARK) 'cd $(REPO) && git pull --ff-only'
label-%: sync  ssh $(SPARK) 'cd $(REPO) && mkdir -p runs && nohup python -m src.label.$* \
                 --config configs/$*.yaml > runs/$(RUN)-$*.log 2>&1 & echo $$!'
logs:          ssh $(SPARK) 'tail -f $$(ls -t $(REPO)/runs/*.log | head -1)'
gpu:           ssh $(SPARK) 'nvidia-smi --query-gpu=memory.used,memory.total --format=csv'
pull-reports:  scp -r $(SPARK):$(REPO)/reports/* ./reports/
```

**Invariant.** Every remote job is detached (`nohup ... &`) and logged to a file. `label-%`
prints the PID and returns immediately. `mkdir -p runs` because `runs/` is gitignored and
will not exist on a fresh clone.

---

## Data contracts

**`configs/schema.json`** — a Draft 2020-12 schema for the `fields` object.
`additionalProperties: false`, 20 known fields, all `type: string`, enums on `level` and
`method`, patterns on `pid`/`status`/`bytes`/`protocol`/`duration_ms`. Carries a
non-standard `x-required-by-shape` table (validators ignore unknown keywords) because
required-ness depends on the shape, which is not part of the fields object.

**`data/gold/logs_gold.jsonl`** — 300 hand-labelled `LogRecord`s, `source="human"`. Frozen.
No code path writes here.

**`data/unseen/unseen.jsonl`** — same format, 2–3 whole shapes absent from gold.

**`reports/<arm>.json`** — one `ArmReport`, `indent=2`.

---

## Build order and gates

| Step | File | Gate before moving on |
|---|---|---|
| 4 | `verify/schema.py`, `verify/exact.py` | valid / schema-invalid / schema-valid-but-wrong all covered |
| 5 | `eval/harness.py` | 5-record toy gold scored against hand-computed literals |
| 6 | `preprocess/*` | 6-line Java trace → 1 record |
| 7 | `label/arm1`, `label/arm0` | both emit a report; arm1 needs no network |
| 8 | `Makefile` | `label-%` produces a detached job and a log file |
