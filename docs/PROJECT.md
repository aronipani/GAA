# parser-lab

A controlled comparison of techniques for training small, locally-deployable specialist
models on parsing tasks (logs first, documents second).

**This is a learning project, not a product.** The deliverable is a report that says
*which technique produced how much accuracy for how much compute on my data*, backed by
runnable code. Optimising for "ship the best parser" and optimising for "understand the
techniques" produce different repos. This is the second one.

---

## The one rule

**The gold set and the eval harness are built first, by hand, and frozen before any
technique runs.**

Everything else in this project is negotiable. This is not. If the gold labels come from
a model, every number you produce measures *agreement with that model*, not accuracy —
and a specialist can never appear to beat its own teacher. One tedious day of manual
labelling is the price of admission for every conclusion that follows.

---

## Milestones

| ID | Milestone | Output | Est. |
|----|-----------|--------|------|
| M0 | Corpus + gold set | 300 hand-labelled log lines, 40+ shapes | 1–2 days |
| M1 | Verification + eval harness | `verify/`, `eval/`, frozen scoring fn | 1–2 days |
| M2 | Baselines (Arm 0, Arm 1) | Zero-shot + classical numbers on the board | 1 day |
| M3 | Arm 3 — teacher distillation | Dataset, SFT'd 3B, scored | 2–3 days |
| M4 | Arm 2 — peer debate loop + ablation | Dataset, SFT'd 3B, scored, 3-way ablation | 3–4 days |
| M5 | Arms 4–5 — rejection sampling, DPO | Two more rows in the table | 2–3 days |
| M6 | Write-up | `reports/findings.md` | 1 day |
| M7 | *(optional)* Documents | Same harness, fuzzy verifier, new lessons | 1 week+ |

M0–M3 is roughly the first week and already answers most of the original question.
Arm 6 (GRPO) is deliberately last and optional — it needs rented H100 time and teaches
the least per hour spent.

---

## The arms

| Arm | Technique | Question it answers |
|-----|-----------|---------------------|
| 0 | Base model, zero-shot + few-shot | What do I get for free? |
| 1 | Drain3 + regex, no LLM | How much do classical methods already give me? |
| 2 | Peer Generator–Adversary–Arbiter loop → SFT | Does same-size peer critique produce usable labels? |
| 3 | Strong teacher (Gemini 2.5 Flash) + verification → SFT | The standard distillation recipe |
| 4 | Rejection sampling, self-generated, verifier-filtered | Can a model bootstrap without a teacher? |
| 5 | DPO/ORPO on pass/fail pairs | What does preference optimisation add over SFT? |
| 6 | GRPO with verifiable reward *(optional, rented GPU)* | Does RLVR beat rejection sampling? |

**Same base model across every arm** — `Qwen2.5-3B-Instruct`. Small enough to train and
re-train quickly on 16GB, big enough to be a real specialist. Changing the base model
between arms destroys the comparison.

### The ablation that answers your original question

Inside Arm 2, run three configurations against the same corpus:

- **2a** Generator + Adversary + Arbiter (the full peer framework)
- **2b** Generator + Adversary, no Arbiter
- **2c** Generator + code referee only, no LLM critic at all

If 2c matches 2a, you have measured the peer arbiter's contribution at zero — on your own
data, with your own models. That is the result worth having, and it is worth more than
any argument about whether debate works.

---

## Why logs first

Logs have an **exact verifier**: a regex either matches and produces the right fields, or
it doesn't. That makes them the only place you can cleanly isolate what each technique
contributes, because the grader introduces no noise of its own.

Documents have a **fuzzy verifier** — schema validity, type checks, and cross-field
consistency will happily pass an extraction that pulled the wrong vendor name. Running
documents second teaches a second, separate lesson: how much your conclusions depended on
having a perfect grader.

---

## Repo layout

```
parser-lab/
├── CLAUDE.md               # working agreement for Claude Code — read this first
├── README.md               # this file
├── ARCHITECTURE.md         # flow diagrams
├── TECHNIQUES.md           # the learning document
├── data/
│   ├── raw/                # collected log corpus, anonymised
│   ├── gold/               # HAND-LABELLED. never written by code. never trained on.
│   │   ├── logs_gold.jsonl
│   │   └── LABELLING_NOTES.md
│   └── generated/          # per-arm training data
│       ├── arm2_peer/
│       ├── arm3_teacher/
│       └── arm4_rejection/
├── src/
│   ├── preprocess/         # drain3 clustering, shape classification, multiline joining
│   ├── verify/             # schema, types, cross-field, exact-match — THE quality signal
│   ├── label/              # one module per arm; identical input/output contract
│   ├── train/              # qlora sft, dpo
│   └── eval/               # frozen harness + report writer
├── configs/                # one yaml per arm
└── reports/                # arm_XX.json, one schema, plus findings.md
```

The layout encodes the important structural point: **`preprocess/`, `verify/`, and
`eval/` are shared infrastructure built once.** Only `label/` varies by arm. Swapping
techniques should be a config change, not a rebuild.

---

## Hardware plan

You have a 12GB 4070 today and a 16GB target. Both work; neither runs everything at once.

| Job | Model | Quant | VRAM |
|-----|-------|-------|------|
| Arm 2 proposer/generator | Qwen2.5-Coder-14B-Instruct | Q4_K_M | ~9GB + KV |
| Arm 2 adversary A (syntax fuzz) | Llama-3.2-3B-Instruct | Q4_K_M | ~2.2GB |
| Arm 2 adversary B (semantic misparse) | Gemma-3-4B-it | Q4_K_M | ~2.8GB |
| Arm 3 teacher | Gemini 2.5 Flash | API | 0 |
| All training | Qwen2.5-3B-Instruct | QLoRA 4-bit | ~10–13GB |

Serve locally through your existing LiteLLM → Ollama gateway so every arm talks to one
OpenAI-compatible endpoint and swapping models is a config line. Ollama will swap models
in and out in seconds; the loop is offline and not latency-sensitive, so prefer the
larger proposer over keeping everything resident.

Full fine-tuning of anything ≥7B does not fit on 16GB. Don't try. QLoRA on 3B is the
realistic slot and is sufficient for a narrow extraction task.

---

## Metrics — every arm reports the same JSON

```json
{
  "arm": "arm3_teacher",
  "field_precision": 0.0, "field_recall": 0.0, "field_f1": 0.0,
  "exact_match": 0.0,
  "schema_compliance": 0.0,
  "per_shape_f1": {"syslog": 0.0, "json_lines": 0.0, "logfmt": 0.0},
  "unseen_shape_f1": 0.0,
  "label_tokens_in": 0, "label_tokens_out": 0, "label_wall_clock_s": 0,
  "train_wall_clock_s": 0,
  "inference_p50_ms": 0, "inference_p95_ms": 0,
  "peak_vram_mb": 0
}
```

Accuracy alone will mislead you. Cost is the entire justification for a small specialist
over just calling the teacher at inference time — if you don't track tokens and latency,
you can't tell whether the specialist was worth training.

`unseen_shape_f1` is the one to watch: hold out 2–3 entire log formats that no arm ever
saw during labelling or training. Generalisation to unseen shapes is the only thing a
trained model gives you that a frozen regex set does not.

---

## Getting started, concretely

1. **Collect the corpus.** 50–200K raw lines from as many distinct sources as you can
   reach — syslog, JSON-lines, logfmt, Apache CLF, k8s, app logs, stack traces. Diversity
   of *shape* matters far more than volume. Anonymise now, not later.
2. **Run Drain3** over it, unsupervised, no GPU. You get template clusters for free and
   an immediate map of how many shapes you actually have.
3. **Sample for the gold set** — stratify across clusters, 300 lines, deliberately
   over-sampling weird ones. Hand-label to your schema. Keep notes on every judgement call
   in `LABELLING_NOTES.md`; you will disagree with yourself by line 200 and the notes are
   what keep the labels consistent.
4. **Hold out 2–3 whole shapes** from the gold set into a separate `unseen/` split.
5. **Build the verifier and eval harness.** Write tests *for the verifier itself* — a
   silently broken grader makes every downstream number noise, and it's the failure mode
   you're least likely to notice.
6. **Run Arms 0 and 1.** Numbers on the board before any training happens.

Then open `TECHNIQUES.md` and start on Arm 3.
