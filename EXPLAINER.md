# What this repo is, in plain English

*A guide to the code for anyone reading it — no jargon assumed. If you want the rules and
the working agreement instead, read `CLAUDE.md`. If you want the reasoning behind specific
choices, read `reports/decisions.md`.*

---

## The one-paragraph version

Logs are messy text. We want a small AI model that can read a log line and pull out the
useful bits — the time, the error level, the message, and so on. There are several
different ways to teach a model to do that, and they cost wildly different amounts of
money and time. This repo is a fair race between those ways. Each way is called an **arm**.
Every arm is fed the same log lines, judged by the same rulebook, and reports the same
numbers, so we can honestly say which one was worth it.

The output of this project is a set of trustworthy numbers, not a shippable product.

---

## The five ideas you need

**1. A log line goes in, a set of fields comes out.**

The line:

```
Aug 22 14:03:11 web-01 sshd[4412]: Accepted publickey for aroni
```

What we want out of it:

```
timestamp = "Aug 22 14:03:11"
host      = "web-01"
process   = "sshd"
pid       = "4412"
message   = "Accepted publickey for aroni"
```

That pair — the original line plus the fields we pulled out of it — is called a
**LogRecord**. It's the atom of this whole project. Everything is a list of these.

**2. "Shape" means what kind of log line it is.**

There are only a handful of common formats, and knowing which one you're looking at tells
you what fields to expect. We recognise six: `syslog`, `json_lines`, `logfmt`,
`apache_clf`, `multiline_trace` (stack traces), and `unknown`.

**3. "Gold" is the answer key.**

A human sat down and hand-labelled 300 log lines correctly. That's the gold set, and it
lives in `data/gold/`. It's the only thing we can measure against, so **no code is ever
allowed to write to it**. If we let a program "improve" the answer key, we'd be marking our
own homework.

There's a second, separate answer key in `data/unseen/`: whole log formats that no arm gets
to practise on. That's how we find out whether an arm actually learned to parse or just
memorised the formats it saw.

**4. An "arm" is one way of doing the job.**

Arm 0 asks a model cold, with no help. Arm 1 uses no AI at all — just hand-written pattern
matching, the way people did this for thirty years before LLMs. Later arms use bigger
models, or several models arguing with each other. Every arm produces the same kind of
output and the same kind of scorecard, which is the only reason comparing them means
anything.

**5. Code judges, never a model.**

An AI can *suggest* an answer. Only plain deterministic code is allowed to decide whether
that answer is right. If we let a model grade its own homework, a friendly grader would
make every arm look good and we'd learn nothing.

---

## Two computers, two jobs

| | The laptop | The Spark |
|---|---|---|
| What it is | A Windows machine with a normal graphics card | A big always-on Linux box with lots of memory |
| What it does | Write code, run tests, run the linter | Run the AI models, do the labelling, do the training |
| How we reach it | You're sitting at it | Only through `make` commands that log in over the network |

Rule of thumb: if a thing needs an AI model, it happens on the Spark, through a `make`
command, in the background, writing to a log file you can read later. Some of these jobs
run for six hours. Nobody watches a six-hour job.

There's one quirk of the Spark worth knowing, because it shapes the code: it can hold
enormous models but it reads memory slowly. Asking it one question at a time is painfully
slow. Asking it 64 questions at once is roughly *fifty times* faster in total. So all our
code sends questions in big batches. There is deliberately no way to send just one.

---

## Every file, and what it's for

### The project files

**`pyproject.toml`** — the shopping list. Which Python version, which libraries, and which
exact versions of them. Pinned to exact versions on purpose: the Spark has an unusual chip
architecture, and "whatever's newest" regularly doesn't work there.

**`.gitignore`** — what *not* to save into version control. The raw logs and the run logs
are huge and live only on the Spark. The answer keys (`data/gold/`, `data/unseen/`) *are*
saved, because both machines must have byte-for-byte identical copies or no comparison is
valid.

**`EXPLAINER.md`** — this file.

### `configs/` — everything adjustable, in one place

The code contains no addresses, no model names, and no magic numbers. They all live here,
so you can change how a run behaves without touching code.

**`hosts.yaml`** — where the two machines live on the network, and the single address that
all AI requests go to.

**`schema.json`** — the rulebook for field names and their formats. It lists every field a
parser is allowed to produce (`timestamp`, `level`, `status`, `method`, …), and what a
valid value looks like — a status code must be three digits starting 1–5, a method must be
one of the real HTTP verbs, and so on. It also records which fields are *required* for each
shape, since a web-server line needs different fields than a stack trace.

**`arm0.yaml`, `arm1.yaml`** — one file per arm: which model it uses, which files it reads
and writes, how many questions to send at once, and the random seed. Arm 1 uses no model at
all, so its file says `llm: null` — spelled out explicitly, because "no model" and "somebody
forgot to fill this in" must not look the same.

### `src/` — the code

**`contracts.py` — the shared vocabulary.**

This defines the handful of things every other file passes around:

- `LogRecord` — a log line and its extracted fields (described above).
- `VerifyResult` — did this record pass the rulebook? If not, a plain-English list of what
  went wrong.
- `ArmReport` — one arm's scorecard. Accuracy, how many tokens it burned, how long it took,
  which random seed it used. **Every arm fills in exactly these fields.** An arm that
  reported something different couldn't be compared with the others, which would defeat the
  entire point of the project.
- `Arm` — the shape of an arm: give it lines, it gives you records back.

It also handles saving and loading. Everything on disk is **JSONL**: a plain text file with
one record per line, so you can open it in any editor, or `grep` it, or read the first ten
lines without loading a two-gigabyte file. Loading is fussy on purpose — if a file has a
misspelled key or a number where text was expected, it stops and tells you the exact line
number, rather than quietly loading something slightly wrong.

One small but important detail: every record gets a `line_id`, a short fingerprint
calculated from the log line itself. The same line always produces the same fingerprint, on
any machine, forever. That's how we match one arm's answer to another's, and to the answer
key, without relying on things being in the same order.

**`config.py` — reading the settings files, strictly.**

It loads the YAML files from `configs/` and turns them into proper typed objects. Its whole
personality is that it refuses to guess:

- Missing setting? Error.
- Setting name misspelled? Error. (This one matters more than it sounds. A typo'd setting
  doesn't cause a crash — it silently does nothing, and you find out days later that your
  run wasn't configured the way you thought.)
- Text where a number belongs? Error. `true` where a number belongs? Also an error, because
  in Python `true` quietly counts as the number 1.
- Zero simultaneous requests? Error — that would hang forever.

It also resolves addresses by name. An arm's config says `host: spark`, and this file looks
up what `spark` actually means in `hosts.yaml`. Pointing an arm at the laptop for a quick
test is a one-word change, and no web address ever appears in the code.

The password for the AI service is read from the computer's environment at the moment it's
needed, not when the config is loaded — so the tests can run with no password set at all.

**`llm.py` — talking to the AI models.**

One file, one job: send a list of questions to the model, get a list of answers back.

- **It only takes lists.** There is deliberately no "ask one question" function, because
  that's the slow way to use this hardware and having the option would mean someone
  eventually uses it.
- **It caps how many run at once.** More is faster right up until the point where it isn't,
  and the limit is a setting, not a guess baked into the code.
- **It answers in the order you asked.** The questions finish in a jumbled order; the
  answers come back matched to your original list.
- **It counts tokens.** Tokens are what you're billed for and what the report compares, so
  they're tracked per batch. If the server forgets to tell us the count, that gets reported
  as a known gap rather than as a comforting zero.
- **It retries sensibly.** "Too many requests" or "server having a moment" → wait and try
  again, waiting longer each time, and honouring the server's own "come back in 2.5 seconds"
  if it sends one. But "your request is malformed" → give up immediately, because trying
  the same broken request five times just breaks it five times.
- **One bad question doesn't sink the batch.** If a single prompt keeps failing, it comes
  back marked as failed while the other 9,999 answers are kept. Losing a six-hour overnight
  run to one awkward log line would be a genuinely bad day.

### `src/preprocess/`, `src/verify/`, `src/label/`, `src/eval/`

Empty so far — these are the next steps. Briefly, what's coming:

- **`preprocess/`** — tidying up before parsing. Most importantly, stack traces span several
  lines and have to be stitched back into one record *first*, or everything downstream
  fights it forever.
- **`verify/`** — the rulebook, in two parts kept deliberately separate. One checks the
  *structure* (is this even a well-formed answer?). The other checks the *content* against
  the answer key (is it actually right?). Splitting them lets us see the interesting failure:
  an answer that's perfectly well-formed and completely wrong — `level="GET"`,
  `method="INFO"` — which no amount of structural checking would ever catch.
- **`label/`** — the arms themselves.
- **`eval/`** — the scorer that produces the final scorecard. Once written it gets frozen,
  because changing how you score after you've measured things invalidates every earlier
  number.

### `tests/`

**`fixtures.py`** — hand-written example log lines, including all the horrible ones on
purpose: JSON with quotes inside quotes inside quotes, accented characters and emoji, a line
chopped off mid-word, a six-line Java stack trace, and the swapped-fields case above. These
are written by hand rather than generated, because a test you can't read is a test you can't
debug at 2am.

**`test_contracts.py`, `test_config.py`, `test_llm.py`** — 68 tests. They run in a fifth of
a second, need no internet, no password, and no graphics card. Most of them check that bad
input *fails* rather than that good input works, because things quietly working slightly
wrong is the failure mode that costs weeks here.

### `reports/`

The only folder that comes back from the Spark. Scorecards, write-ups, and
`decisions.md` — a running log of every judgement call and its one-line reason.

---

## How you actually use it

```
make test           # on the laptop: run the tests and the linter. Fast, offline.
make sync           # push your code and pull it down on the Spark
make label-arm0     # start arm 0 labelling on the Spark, in the background
make logs           # watch what the most recent job is printing
make gpu            # check how much memory the Spark is using
make pull-reports   # bring the scorecards back to the laptop
```

The pattern behind all of them: you never type a remote login command yourself, and no job
ever runs in the foreground where closing your laptop would kill it.

---

## The rules, and why each one exists

1. **Never write to the answer key.** It's the only ground truth there is. If a label looks
   wrong, write the complaint down in `reports/gold_disputes.md` and stop — don't fix it
   mid-experiment.
2. **Never train or test on the answer key.** Studying the exam paper doesn't prove you
   learned the subject. This is checked by code, not trusted to memory.
3. **No AI decides anything.** Models propose; code judges.
4. **Nothing hardcoded.** Addresses, model names, file paths and numbers all live in
   `configs/`.
5. **Always batch.** One-question-at-a-time is roughly fifty times slower on this hardware.
6. **Check the Spark can run a library before adding it.** It has an unusual chip; a lot of
   popular packages simply don't build there.
7. **No AI "agent frameworks".** These jobs are about 150 lines of ordinary code talking to
   a web address. A framework would add more concepts than it removes.
8. **Same everything across arms.** Same base model, same seed, same scorer, same report
   fields. The moment one arm differs quietly, every comparison in the project becomes
   worthless — and worse, still looks fine.
