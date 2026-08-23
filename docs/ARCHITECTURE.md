# Architecture

## The structural idea

Three stages are **shared infrastructure** built once and reused by every technique:
preprocessing, verification, and evaluation. Only **label generation** varies by arm.

Draw the boundary here and swapping techniques is a config change. Draw it anywhere else
and each arm becomes a rebuild, the arms stop being comparable, and the comparison — the
entire point of the project — quietly dies.

```mermaid
flowchart TB
    subgraph SHARED1["SHARED — build once"]
        RAW[Raw corpus<br/>50-200K lines, many shapes]
        PRE[Preprocess<br/>Drain3 clustering · shape classifier<br/>multiline joining]
        RAW --> PRE
    end

    PRE --> SPLIT{Split}
    SPLIT -->|300 lines, stratified| GOLD[["GOLD SET<br/>hand-labelled by you<br/>FROZEN · never trained on"]]
    SPLIT -->|2-3 whole shapes| UNSEEN[["UNSEEN SHAPES<br/>generalisation test"]]
    SPLIT -->|remainder| POOL[Unlabelled pool]

    POOL --> ARMS

    subgraph ARMS["SWAPPABLE — one module per arm, identical I/O contract"]
        direction LR
        A1[Arm 1<br/>Drain3 + regex]
        A2[Arm 2<br/>Peer G-A-A loop]
        A3[Arm 3<br/>Teacher distill]
        A4[Arm 4<br/>Rejection sampling]
    end

    ARMS --> VERIFY

    subgraph SHARED2["SHARED — build once"]
        VERIFY[Programmatic verification<br/>schema · types · cross-field · exact match]
        DATA[(Verified dataset<br/>JSONL)]
        TRAIN[QLoRA SFT<br/>Qwen2.5-3B]
        VERIFY --> DATA --> TRAIN
    end

    TRAIN --> EVAL
    GOLD --> EVAL
    UNSEEN --> EVAL

    subgraph SHARED3["SHARED — frozen before arms run"]
        EVAL[Eval harness<br/>field P/R/F1 · exact match<br/>tokens · latency · VRAM]
        REPORT[reports/arm_XX.json]
        EVAL --> REPORT
    end

    style GOLD fill:#8b1a1a,color:#fff
    style UNSEEN fill:#8b1a1a,color:#fff
    style VERIFY fill:#1a4d2e,color:#fff
    style ARMS fill:#2c3e50,color:#fff
```

---

## Technique A — Peer debate loop (Arm 2)

Three same-size models. **The referee is code, not a model.** The loop's output is a
*parser*, and the parser then labels the pool for free.

```mermaid
flowchart TB
    START([Corpus sample<br/>+ currently failing lines]) --> PROP

    PROP["<b>PROPOSER</b> — Qwen2.5-Coder-14B<br/>writes named-capture regexes / grammar"]
    PROP --> COMPILE{"re.compile()<br/>valid?"}
    COMPILE -->|no, microseconds| PROP

    COMPILE -->|yes| REF["<b>REFEREE — CODE, NOT A MODEL</b><br/>run parser over regression corpus<br/>per-shape coverage · field-type checks"]

    REF --> BETTER{"beats current best<br/>AND passes 100%<br/>of regression corpus?"}
    BETTER -->|no| PROP
    BETTER -->|yes| ACCEPT[["Accept · freeze into parser<br/>append to accepted-patterns library"]]

    ACCEPT --> ADV

    subgraph ADVS["ADVERSARIES — cheap, parallel, fuzzers not critics"]
        ADV1["Adversary A — Llama-3.2-3B<br/>syntax edge cases:<br/>escaped quotes · unicode · truncation"]
        ADV2["Adversary B — Gemma-3-4B<br/>semantic misparse:<br/>right regex, wrong field name"]
    end

    ADV[Generate attack lines] --> ADVS
    ADVS --> RUN{"parser fails<br/>any line?"}

    RUN -->|yes| RECORD["Record line + expected parse<br/>→ regression corpus grows permanently"]
    RECORD --> PROP

    RUN -->|"no, N rounds running"| DONE([Converged — freeze parser<br/>models never run again])

    DONE --> LABEL["Parser labels the whole pool<br/>millions of pairs, zero inference"]
    LABEL --> SFT([→ shared verification → SFT])

    style REF fill:#1a4d2e,color:#fff
    style RECORD fill:#7d5a00,color:#fff
    style DONE fill:#2c3e50,color:#fff
```

**Three properties that make this work, all of them structural rather than clever:**

1. **The ratchet.** Every counterexample enters the regression corpus permanently, with
   its expected parse. A new candidate is accepted only if it passes 100% of the corpus.
   The parser can only move forward. Same shape as a git gate — enforcement that doesn't
   depend on the model cooperating.
2. **No conversation history.** Each round hands the proposer the current parser and the
   lines it currently fails — never a transcript of prior argument. Long debate history is
   what makes small models capitulate to whichever position sounded most confident.
   Starving them of it is the design, not an optimisation.
3. **Inference scales with *shapes*, not examples.** Maybe 20–50 shapes, then the frozen
   regexes label the entire corpus for nothing. A per-example debate architecture would
   spend 3 models × 3 rounds × N examples to produce N labels — orders of magnitude more
   compute for a smaller and worse dataset.

### The ablation

```mermaid
flowchart LR
    C[Same corpus<br/>same budget] --> A["<b>2a</b> Generator<br/>+ Adversary<br/>+ Arbiter"]
    C --> B["<b>2b</b> Generator<br/>+ Adversary"]
    C --> D["<b>2c</b> Generator<br/>+ code referee only"]
    A --> R[Compare coverage,<br/>rounds-to-converge,<br/>tokens spent]
    B --> R
    D --> R
    style D fill:#1a4d2e,color:#fff
```

If **2c ≈ 2a**, the peer arbiter contributed nothing measurable. That is a real finding,
produced on your data, and it is the thing you actually set out to learn.

---

## Technique B — Teacher distillation (Arm 3)

```mermaid
flowchart TB
    IN([Unlabelled pool]) --> PREP["Preprocess<br/>logs: shape-classified<br/>docs: Docling/Marker → clean text"]

    PREP --> TEACH["<b>TEACHER</b> — Gemini 2.5 Flash<br/>extract to schema<br/>k samples at temperature"]

    TEACH --> SC{"self-consistency:<br/>do k samples agree?"}
    SC -->|no| HARD[["Flag as HARD<br/>→ hand-label a slice<br/>DO NOT silently discard"]]
    SC -->|yes| VER

    VER["<b>VERIFICATION</b><br/>schema · types · enums<br/>cross-field consistency<br/>regex validators"]

    VER --> PASS{passes?}
    PASS -->|no| HARD
    PASS -->|yes| KEEP[(Verified dataset)]

    KEEP --> SFTT[QLoRA SFT · Qwen2.5-3B]
    SFTT --> EV([→ shared eval])

    HARD -.->|"track pass-rate BY SHAPE<br/>this is your blind spot"| AUDIT[/"reports/coverage_gaps.md"/]

    style VER fill:#1a4d2e,color:#fff
    style HARD fill:#8b1a1a,color:#fff
```

**The failure mode to instrument for: selection bias.** "Keep only what passes
verification" means training exclusively on the subset your teacher found easy. Hard
layouts, unusual formats and ambiguous fields get dropped *systematically*, and the
training set ends up with a hole shaped precisely like your hardest cases.

Break the pass rate out by shape. If one category passes at 30% while others hit 95%,
hand-label a slice of that category's failures rather than discarding them.

---

## What differs between logs and documents

```mermaid
flowchart LR
    subgraph L["LOGS — exact verifier"]
        L1[Raw line] --> L2[Regex / grammar] --> L3[Fields]
        L3 --> L4{{"matches gold exactly?<br/>binary, no ambiguity"}}
    end

    subgraph D["DOCUMENTS — fuzzy verifier"]
        D1[PDF bytes] --> D2["Docling / Marker / olmOCR<br/><b>layout — the actual hard part</b>"]
        D2 --> D3[Clean text + hints] --> D4[Model extraction] --> D5[Fields]
        D5 --> D6{{"schema-valid, correctly typed,<br/>arithmetically consistent —<br/>and still the wrong vendor name"}}
    end

    style L4 fill:#1a4d2e,color:#fff
    style D6 fill:#8b1a1a,color:#fff
```

The small model **never sees raw PDF bytes** — only cleaned text plus layout hints.
Reading order, multi-column, tables spanning pages, scanned pages: that's the layout
tools' job, and they already do it well. A text-only 3B fine-tune cannot learn it and
shouldn't try.

Consequence for the comparison: on documents your verifier catches structural and
arithmetic errors but not wrong-value-right-shape errors, so the gold set carries far more
of the weight. Trust the document numbers less than the log numbers, and say so in the
write-up.
