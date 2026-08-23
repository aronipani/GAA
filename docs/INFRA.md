# INFRA — DGX Spark as server, laptop as cockpit

Two machines, two jobs, one rule: **the laptop never runs a model bigger than the
deployment target, and the Spark never runs an editor.**

| | Laptop (RTX 4070, 12GB, Windows) | DGX Spark (GB10, 128GB unified, ARM64) |
|---|---|---|
| Role | Write code, run tests, drive Claude Code | Serve models, generate data, train |
| Runs | pytest, ruff, preprocessing, eval scoring | Ollama/llama.cpp, TRL/PEFT training jobs |
| Models | 3B smoke tests only | everything else |
| Data | nothing durable — clone of repo | `data/`, checkpoints, model cache |
| Uptime | when you're at it | always on, headless |

The laptop keeps a second, non-obvious job: it is your **deployment-realism check**. The
whole thesis is a specialist that runs on modest hardware. If the trained 3B doesn't hit
target latency on the 4070, the project failed regardless of what F1 says on the Spark.

---

## The one number that shapes every design decision

The Spark has 128GB of unified memory at **273 GB/s**. The capacity is extraordinary for
the size; the bandwidth is not. Decode speed on a dense model is bandwidth-bound, so:

| Workload | Throughput | Source |
|---|---|---|
| 14B NF4, single stream | ~19 tok/s | community benchmark |
| 32B FP4, single stream | ~10 tok/s | community benchmark |
| 72B NF4, single stream | ~3.8 tok/s | community benchmark |
| Llama 3.1 8B FP4, **batch 128** | ~924 tok/s | StorageReview |
| Qwen3-Coder-30B-A3B FP8, **batch 64** | ~483 tok/s | StorageReview |
| 7B LoRA training | ~2.6 samples/s | community benchmark |

Read those last two rows again. **Concurrency is worth ~50–100x on this box.** A 70B
answering one prompt at a time is painful; the same box answering 64 prompts at once is a
data factory.

### Design rules that follow

1. **Everything is a batch job.** No interactive loops, no request-response waiting. Arms
   submit N prompts, poll, and write JSONL. If a design needs a fast single response, the
   design is wrong for this hardware.
2. **Run overnight, not while you watch.** The labelling passes are offline by nature.
   Kick them off, read the log in the morning.
3. **Prefer MoE for the big roles.** A 30B-A3B MoE has ~3B active parameters per token, so
   it decodes like a small model while reasoning like a large one — the single best fit for
   a bandwidth-starved, memory-rich box. Prefer it over a dense 32B for the proposer.
4. **Big dense models are for quality ceilings, not volume.** Use a 70B to label a few
   thousand hard examples, not a million easy ones.
5. **Start memory utilisation at 0.5, not 0.8.** Users report hard system freezes when KV
   cache allocation pushes past ~80% of the pool, and the official vLLM DGX Spark recipe
   sets `--gpu-memory-utilization 0.5` where the same model on an RTX Pro 6000 gets 0.9.
   Raise it one deliberate step at a time. The unified pool is shared with the OS; there is
   no separate VRAM to fall back on.

---

## Software reality on ARM64 + sm_121

GB10 reports compute capability **sm_121**, and most of the ecosystem targets sm_120 or
below. This is the main friction of the machine, and it's the thing to plan around rather
than discover at 2am.

| Component | Status | What to do |
|---|---|---|
| llama.cpp / Ollama | Works well | **Start here.** Ships its own Blackwell-capable backend. |
| PyTorch | Works with warnings | Official wheels stop at sm_120. Use NGC containers or community aarch64 wheels. |
| transformers / PEFT / TRL | Work | SFT, DPO, ORPO all fine |
| Unsloth | Works | Recommended path for fine-tuning here |
| bitsandbytes | Works | NF4/FP4 tested on aarch64 builds |
| Triton | Works with env fix | `TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas` |
| flash-attention | **Does not build** | Use PyTorch SDPA. Plan for it; don't fight it. |
| TransformerEngine / FP8 training | **Broken** | Train in bf16. No FP8 shortcuts. |
| vLLM | Docker/source only, fiddly | Defer. Ollama first; revisit only if throughput blocks you. |

**Superseded — see `SERVING.md`.** The vLLM recipes catalogue now lists DGX Spark as
supported hardware with tested launch commands and NVIDIA-published containers, so vLLM is
the plan rather than a later optimisation. It brings continuous batching, chunked prefill
and MTP speculative decoding, which are precisely the mitigations for rule 1 above. Ollama
remains the fallback if container work stalls. Pin image digests; validate flags against
the exact image rather than copying between recipes.

**No ECC memory.** LPDDR5X here is unprotected. For a learning project that's acceptable;
just know that an unreproducible weird result might be a bit-flip rather than your bug, and
that a model you intend to actually deploy should get a final confirmation run somewhere
with ECC.

Verify the current state before building — this ecosystem moves monthly:
`nvidia-smi`, `python -c "import torch; print(torch.cuda.get_device_capability())"`.

---

## Model roster and memory budget

128GB means **no swapping** — every role stays resident, which is the single biggest
workflow upgrade over the 12GB plan.

| Role | Model | Format | Resident |
|---|---|---|---|
| Arm 2 proposer | Qwen3-Coder-30B-A3B (MoE) | FP8 / Q5 | ~30GB |
| Arm 3b large teacher | Qwen2.5-72B or Llama-3.3-70B | NF4/Q4 | ~40GB |
| Adversary A (syntax fuzz) | Llama-3.2-3B-Instruct | Q4 | ~2GB |
| Adversary B (semantic misparse) | Gemma-3-4B-it | Q4 | ~3GB |
| LLM arbiter (hybrid, second gate) | reuse the 30B MoE | — | 0 |
| Student under training | Qwen2.5-3B / 8B | bf16 | ~6–16GB train-time |

That totals ~75–90GB. Keep the ceiling at ~100GB and leave the rest for KV cache and the
OS. Load the 70B only for the arm that needs it; at 3.8 tok/s unbatched it is a specialist
tool, not a default.

**Spend the size budget asymmetrically.** Writing a correct grammar for a gnarly log format
is hard; generating a line that breaks a parser is easy. 30B proposer + 3B adversaries beats
splitting the budget evenly.

---

## Training changes now that memory isn't the constraint

The 16GB plan called for QLoRA because nothing else fit. That constraint is gone, and the
recipe should change with it:

- **bf16 LoRA instead of QLoRA** for 7–8B students. Quantised base weights were a memory
  workaround; without the memory pressure they only cost you quality and debugging clarity.
- **Full-parameter fine-tuning of an 8B is on the table**, and of a 3B is comfortable. Worth
  running as its own comparison row — full FT vs. LoRA on the same data is a clean ablation
  and you now have the memory to ask the question.
- **bf16 only.** No FP8 training path here.
- **SDPA attention**, not flash-attn. Expect somewhat lower throughput and longer sequences
  costing more; size your `max_seq_len` accordingly (2–4K is plenty for log lines, 8K for
  document chunks).
- **Checkpoint often.** No ECC, single box, long jobs.

Rough expectation from the community 7B-LoRA figure (~2.6 samples/s): a 50K-example epoch
lands in the 5–6 hour range. Overnight, not coffee-break. Plan the day around it.

---

## Wiring the two machines

```
┌─────────────────────────────┐         ┌──────────────────────────────────┐
│  LAPTOP (Windows, 4070)     │         │  DGX SPARK (DGX OS, headless)    │
│                             │   ssh   │                                  │
│  Claude Code ──► repo edits │────────►│  /srv/parser-lab  (git clone)    │
│  pytest / ruff (local)      │         │    ├── data/     ← lives here    │
│  eval scoring on small sets │         │    ├── runs/     ← logs, ckpts   │
│  3B smoke test via Ollama   │         │    └── models/   ← HF cache      │
│                             │  :4000  │                                  │
│  LiteLLM client ────────────┼────────►│  LiteLLM gateway :4000           │
│                             │         │    └─► Ollama :11434             │
└─────────────────────────────┘         └──────────────────────────────────┘
         git push/pull  ◄──── GitHub ────►  git pull
```

**Code moves by git, never by rsync.** Both machines clone the same repo; the laptop pushes,
the Spark pulls. Data never leaves the Spark — it's large, and for real Prologis logs it
shouldn't be on a laptop anyway.

**One endpoint.** Everything — every arm, every script, on either machine — talks to the
LiteLLM gateway on the Spark. Model choice is a config string. Nothing anywhere hardcodes
`localhost:11434` or a model name.

```yaml
# configs/hosts.yaml
spark:
  host: spark.lan            # or Tailscale name
  user: aroni
  repo: /srv/parser-lab
  gateway: http://spark.lan:4000/v1
laptop:
  gateway: http://localhost:11434/v1   # smoke tests only, 3B only
```

Bind the gateway to the LAN interface, not `0.0.0.0` on an untrusted network, and put a key
on it. It is an unauthenticated remote-inference endpoint by default.

If the laptop moves around, Tailscale between the two removes the whole VPN/port-forward
problem and gives you stable names.

---

## Makefile — the whole remote interface

Claude Code should never type an `ssh` command. It calls `make` targets, which handle the
hop, run detached, and log to a file it can read back.

```makefile
SPARK := aroni@spark.lan
REPO  := /srv/parser-lab
RUN   := $(shell date +%Y%m%d-%H%M%S)

sync:                       ## push local commits, pull them on the Spark
	git push
	ssh $(SPARK) 'cd $(REPO) && git pull --ff-only'

test:                       ## LOCAL — fast, no GPU, runs on the laptop
	pytest -q && ruff check .

serve-up:                   ## start gateway + model host on the Spark
	ssh $(SPARK) 'cd $(REPO) && docker compose up -d'

serve-status:
	ssh $(SPARK) 'curl -s localhost:4000/v1/models | head -40'

label-%: sync               ## make label-arm3 → detached batch job on the Spark
	ssh $(SPARK) 'cd $(REPO) && nohup python -m src.label.$* \
	  --config configs/$*.yaml > runs/$(RUN)-$*.log 2>&1 & echo $$!'

train-%: sync
	ssh $(SPARK) 'cd $(REPO) && nohup python -m src.train.$* \
	  --config configs/$*.yaml > runs/$(RUN)-$*.log 2>&1 & echo $$!'

logs:                       ## tail the newest run log
	ssh $(SPARK) 'tail -f $$(ls -t $(REPO)/runs/*.log | head -1)'

gpu:
	ssh $(SPARK) 'nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu \
	  --format=csv'

pull-reports:               ## bring results back for inspection
	scp -r $(SPARK):$(REPO)/reports/* ./reports/
```

Every remote job is **detached and logged**. A dropped SSH session must not kill a six-hour
training run, and Claude Code needs a file to read rather than a stream to watch.

---

## Rules for Claude Code

Add these to `CLAUDE.md`:

1. **Two machines. Know which one you're on.** Local = editing, tests, lint. Remote = models,
   data, training. If a command needs a GPU, it goes through a `make` target.
2. **Never start a training or labelling run in the foreground.** Detached + logged, always.
3. **Never load a model above ~4B on the laptop.** 12GB. It will swap, thrash, and waste an
   hour. Smoke tests only.
4. **No hardcoded endpoints or model names.** `configs/hosts.yaml` and the arm config, or
   it doesn't ship.
5. **Assume ARM64 on the remote.** Before adding a dependency, check it has an aarch64
   wheel. `flash-attn`, `TransformerEngine`, and anything expecting x86 CUDA wheels will
   fail there while passing on the laptop. Pin versions.
6. **Batch, don't loop.** Any code that issues one inference request and waits is wrong for
   this hardware. Submit lists, use concurrency, write JSONL.
7. **Cap `gpu_memory_utilization` at 0.80.** Higher has hard-locked this machine.
8. **`data/` and `runs/` are Spark-only.** Never commit them; never scp datasets to the
   laptop. `reports/` comes back, nothing else.

---

## Day-1 bootstrap

```
□ Spark reachable by name; SSH key auth; Tailscale if the laptop roams
□ nvidia-smi + torch.cuda.get_device_capability() → confirm 12.1 and CUDA version
□ Ollama running; pull one 3B and one 30B MoE; time a single completion
□ Benchmark concurrency: same prompt at batch 1, 8, 32, 64 — record tok/s
      → this table sizes every batch parameter in the project. Do it first.
□ LiteLLM gateway up on :4000, keyed, LAN-bound; reachable from the laptop
□ git clone on both; make sync round-trips
□ NGC PyTorch container pulled and pinned; verify_install-style smoke check
□ One throwaway LoRA run on 500 examples end-to-end — prove the path before it matters
□ Same 3B pulled on the laptop for deployment-realism latency checks
```

The concurrency benchmark is the one that pays for itself immediately. Every "how many
prompts per batch" decision downstream comes from that table, and guessing wrong costs you
an order of magnitude on a box where concurrency is worth 50–100x.

---

## Sources

Hardware and throughput figures: NVIDIA DGX Spark product page and datasheet; LMSYS
in-depth review; StorageReview review (batched throughput); community benchmarks in
`ogulcanaydogan/dgx-spark-llm-stack` (single-stream and training figures, compatibility
matrix). sm_121 issues: vLLM issues #31128, #36821, #37431; NVIDIA developer forums.

This ecosystem is moving fast. Re-verify the compatibility table before you rely on it.
