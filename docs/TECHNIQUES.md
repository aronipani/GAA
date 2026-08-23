# Techniques — the learning document

Read this before building each arm. Each section covers: what the technique is, the
mechanism that makes it work, what you should expect to see, and the failure mode that
will bite you.

---

## 0. The frame: where does the learning live?

The single most useful distinction in this project, and the one that determines whether a
given technique can work at all:

| | Where progress accumulates | Survives process restart? | Generalises to new inputs? |
|---|---|---|---|
| **Debate loop (no training)** | The parser + regression corpus | Yes — in your files | No — regexes only cover what they cover |
| **Retrieval library** | JSONL of accepted patterns | Yes | Weakly — warm start on similar shapes |
| **SFT / DPO / RLVR** | Model weights | Yes | Yes — the whole point |

A debate loop with no weight updates improves *nothing about the models*. Qwen after 200
rounds is byte-identical to Qwen before. That's not a defect when the artifact you want is
a deterministic parser — it's the good property. But it means the loop can only ratchet if
there is **external state to write to**.

Log parsing has that external state: the parser. Physics problem-solving does not, which
is why the same architecture that works here fails there. Carry that question into every
technique you evaluate: *what is the durable artifact, and what writes to it?*

---

## 1. Classical baseline — Drain3 (Arm 1)

**What it is.** Unsupervised log template extraction. Drain3 builds a fixed-depth parse
tree over tokens and clusters lines into templates like
`Connection from <*> port <*> closed`. No model, no GPU, effectively free.

**Why it goes first.** It's the honest baseline, and half the time it's most of the
answer. The `logparser` toolkit benchmarks show classical methods scoring very well on
standard log corpora. If you skip this, you risk concluding that your fine-tune "achieves
90%" without knowing that thirty lines of Drain3 achieved 88%.

**Where LLMs actually earn their place** is what Drain3 *doesn't* do: semantic field
naming (`<*>` → `source_port`), type inference (`dur=1.2s` is a duration, not a string),
and deciding which template variables are worth capturing at all. That gap is the real
scope of the LLM work, and it's much narrower than "parse logs" sounds.

**Expected result:** high template-clustering accuracy, zero semantic field naming.

---

## 2. Peer debate — Generator / Adversary / Arbiter (Arm 2)

**What it is.** Multiple same-size models in assigned roles, iterating: one produces, one
attacks, one judges.

**The mechanism, honestly stated.** Debate helps when the critic can *discriminate*
better than the generator can *generate*. For small models on hard judgement tasks, that
gap is usually absent or negative — a 7–14B critic produces confident false positives and
misses real errors. The documented failure mode in multi-agent setups is **convergence,
not divergence**: models capitulate to whichever position was stated more assertively, so
later rounds often launder the first round's answer into higher stated confidence.

**But it's a good fit here anyway**, for one specific reason: *generating a failing input
is cheap to verify*. The adversary doesn't need to be right — it just emits candidate log
lines, and your code runs the parser to find out. A model that would be a mediocre critic
is a perfectly adequate fuzzer. Verification asymmetry is what rescues the technique.

**Design rules that follow:**
- Referee is code. Never an LLM opinion in the accept/reject path.
- No conversation history between rounds. Pass state, not transcript.
- The arbiter role probably has nothing to do — that's what the 2a/2b/2c ablation tests.
  Prefer a second adversary with a different prompt bias.
- Strict output contract on the proposer (named groups only, `re` syntax, no lookbehind),
  validated with `re.compile()` before the corpus ever sees it. A large share of failures
  are syntax errors; catching them in microseconds keeps round count down.

**Expected result:** works, converges, and 2c (no LLM critic) plausibly matches 2a. Take
that seriously rather than being disappointed by it.

**Terms to read up on:** CEGIS (counterexample-guided inductive synthesis) — this loop is
a direct instance of it, and the program-synthesis literature has thought harder about
convergence than the agent literature has.

---

## 3. Teacher distillation (Arm 3)

**What it is.** A significantly stronger model labels; programmatic checks filter; the
small model trains on survivors. This is the standard, boring, effective recipe for
producing a narrow specialist.

**Why it beats peer debate for label quality.** You're buying a genuine capability gap.
Gemini 2.5 Flash is meaningfully better than a local 7B at reading an unfamiliar log
format, and it costs cents. Three 7Bs arguing is an elaborate way of avoiding paying for
that gap — and producing worse labels in the process.

**Design notes:**
- Sample k times at temperature; disagreement across samples is a free difficulty signal.
- Verification is the quality filter, not the teacher's confidence. Teachers are
  confidently wrong in exactly the cases you care about.
- Track pass rate **by shape**. This is where selection bias hides.

**Expected result:** the strongest arm, or close to it, at low cost. If it isn't, suspect
your verifier before you suspect the technique.

**The number that matters:** specialist vs. teacher on the gold set. Beating the teacher
is possible — the verifier filters the teacher's errors out of training data, so the
student learns a cleaner function than the teacher computes. Matching the teacher at 100x
lower cost is already a win.

---

## 4. Rejection sampling / STaR (Arm 4)

**What it is.** The model generates k candidates for each input; the verifier keeps only
the passing ones; SFT on the survivors. Repeat. No teacher, no reward model, no RL
machinery — just sampling and filtering.

**Why it's worth an arm.** It's the cheapest thing that turns a verifier into a training
signal, and it's remarkably strong. Much of what people attribute to RL is available from
rejection sampling at a fraction of the complexity. It also directly answers "can this
bootstrap without a teacher?", which matters if the endgame is fully local.

**The constraint:** you can only train on problems the model already solves *sometimes*.
Inputs it never gets right contribute nothing, so hard shapes stay hard. Expect the pass
rate to be very uneven across shapes and use that as a difficulty map.

**Terms:** STaR, rejection sampling fine-tuning, expert iteration.

---

## 5. Preference optimisation — DPO / ORPO (Arm 5)

**What it is.** Train on (preferred, rejected) pairs directly, no reward model. ORPO folds
this into SFT in one stage; DPO is a second stage after SFT.

**Honest expectation: this will add less than the docs promise.** Preference optimisation
earns its keep where the verifier is *soft* and "better" is a matter of degree. Your log
verifier is binary — the regex matches or it doesn't — so rejection-sampled SFT already
captures nearly all the available signal. Run it because the mechanics are worth
understanding and because measuring a small delta is a legitimate result.

**Where it should help more:** documents, where cross-field consistency and formatting
quality are graded on a spectrum rather than a boolean.

**Pair construction matters.** Pairs that differ in many ways at once teach little.
Prefer minimal pairs — same input, one verified output, one that failed a *specific*
check — over "final polished version vs. first messy attempt".

---

## 6. RLVR — GRPO with verifiable reward (Arm 6, optional)

**What it is.** Reinforcement learning where reward is a programmatic pass/fail rather
than a learned reward model. GRPO drops the value network and normalises advantages within
a group of sampled completions.

**Why it's last.** Rollouts plus a reference model won't fit on 16GB — that's rented H100
territory. And the honest prior is that it beats rejection sampling by a modest margin on
a task this narrow. Run it to learn the machinery, not because the project needs it.

**Run it only after arm 4 plateaus.** If rejection sampling is still improving, GRPO is
solving a problem you don't have yet.

---

## 7. What "small specialist" actually buys you

Worth being clear-eyed about, since it's the thesis of the whole project:

**Real wins:** inference cost, latency, data never leaves your network, no rate limits,
deterministic deployment, no vendor version drift breaking your extraction overnight.

**Not a win:** raw accuracy on generic schemas. Off-the-shelf models already parse
invoices and syslog well. A fine-tune matches them more cheaply; it rarely beats them.

**The case where it genuinely wins on accuracy: proprietary schemas.** Fields that exist
only in your organisation's head — lease abstracts, CRE deal terms, internal log formats,
domain-specific document types. No off-the-shelf model knows the schema, few-shot prompting
runs out of context, and a small specialist is the right answer rather than a cheap one.

**The cost nobody prices in:** a specialist per task means N models, N eval sets, N
retraining cycles as formats drift. Two or three high-value schemas is a program. A dozen
is a maintenance burden that quietly exceeds the inference savings.

---

## 8. Reading order

1. Drain3 / `logparser` benchmarks — the classical baseline you must beat
2. STaR and rejection-sampling fine-tuning — the core loop of arms 3–4
3. CEGIS / program synthesis — the correct frame for arm 2
4. DPO and ORPO papers — enough to build arm 5 properly
5. GRPO — only when you get to arm 6
6. Docling / Marker / olmOCR docs — before touching documents at all

---

## 9. The write-up

`reports/findings.md` should answer, in order:

1. What did each arm score on the frozen gold set, and at what token and wall-clock cost?
2. What did each score on **unseen shapes** — the only number where a trained model can
   beat a frozen regex set?
3. **Did the arbiter contribute anything measurable** (2a vs 2b vs 2c)?
4. Did the 3B specialist match or beat its teacher, and at what cost ratio?
5. Where did the verifier lie to you? Every project like this has at least one case where
   a passing extraction was wrong, and finding it is worth more than another point of F1.
6. What changed when you moved to documents and lost the exact verifier?

If you can answer those six with numbers from your own runs, the project succeeded —
regardless of which technique won.
