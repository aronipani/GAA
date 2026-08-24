# data/

| Directory | Committed? | Lives on | Holds |
|---|---|---|---|
| `raw/` | no, gitignored | **Spark only** | 50–200K collected log lines, anonymised on ingest |
| `generated/` | no, gitignored | Spark | per-arm predictions, `armN.jsonl` |
| `gold/` | **yes** | both machines | `logs_gold.jsonl` — 300 hand-labelled records, frozen |
| `unseen/` | **yes** | both machines | `unseen.jsonl` — 2–3 whole held-out shapes, frozen |

`raw/` and `generated/` are not in git, so they do not exist on a fresh clone. Create them
on the Spark before the first run:

```bash
ssh aroni@spark.lan 'mkdir -p /srv/parser-lab/data/{raw,generated} /srv/parser-lab/runs'
```

Do not create them on the laptop. Local development uses `tests/fixtures.py` — hand-built
lines you can read — which is what keeps `make test` working with no network and no data.

## raw/

One file per source, so the shape distribution is visible before Drain3 runs and an odd
cluster can be traced back to where it came from.

```
raw/
├── syslog-web01.log
├── app-json.jsonl
├── nginx-access.log
└── java-app.log
```

Diversity of *shape* matters far more than volume. **Anonymise on ingest**, not later —
once raw PII is on disk, it is on disk.

## gold/ and unseen/

Hand-labelled by a human, and frozen. No script, no arm, no helpful augmentation. If a
label looks wrong, append to `reports/gold_disputes.md` and stop.

They are committed because the comparison is meaningless if the two machines disagree
about the answer key. One `LogRecord` per line, `source="human"`.
