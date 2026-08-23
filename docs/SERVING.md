# SERVING — vLLM on DGX Spark

**This supersedes the "Ollama first, vLLM deferred" stance in `INFRA.md`.** The vLLM
recipes catalogue now lists DGX Spark (GB10) as explicitly supported hardware with
tested launch commands, which removes the reason for deferring. Ollama stays as a
fallback, not the plan.

Everything below is transcribed from the vLLM recipes site (`recipes.vllm.ai`), which
is maintained by the vLLM project. Several of these models are recent; treat the
capability claims as the vendors' and the flags as tested, but verify both on your box
before building an arm on top of them.

---

## Why this changes things

`--async-scheduling`, chunked prefill, prefix caching, and MTP speculative decoding are
the specific mitigations for a bandwidth-bound machine. Speculative decoding in
particular attacks the exact bottleneck: it trades the Spark's abundant compute for
fewer memory round-trips per token. Ollama gives you none of these.

The cost is operational: vLLM on ARM64 + sm_121 wants a pinned container, and flags do
not transfer between models or image versions. Copying a flag set from one recipe to
another is how you silently regress quality or throughput.

---

## Containers

| Use | Image |
|---|---|
| NVIDIA-published, Spark-oriented | `nvcr.io/nvidia/vllm:25.12.post1-py3` |
| Upstream, needed for NVFP4 ModelOpt checkpoints | `vllm/vllm-openai:v0.24.0-ubuntu2404` |

**Pin by digest, not tag.** Then record the digest in `configs/serving.yaml` next to the
flags it was validated with. When something breaks in three weeks, the pair is what lets
you tell a model problem from an image problem.

---

## Model roster

Revised for the Spark, with NVFP4 as the native Blackwell 4-bit format.

| Role | Model | Format | Notes |
|---|---|---|---|
| **Proposer / arbiter (Arm 2)** | `nvidia/Qwen3.6-35B-A3B-NVFP4` | NVFP4 | 35B total, **3B active** — the ideal shape for this box. Multimodal, 256K ctx. |
| **Alt proposer** | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16` | BF16 | 30B/3B active, DGX Spark listed as supported. **Mamba-hybrid — see risks.** |
| **Large teacher (Arm 3b)** | `Qwen/Qwen3.5-27B` or `Qwen/Qwen3.8-27B` | FP8/NVFP4 | Dense; slower decode, use for the hard slice only |
| **Adversaries** | `Qwen/Qwen3.5-2B`, `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` | BF16 | Fuzzing is a low bar; keep these tiny |
| **Student base** | `Qwen/Qwen3.5-4B` or `Qwen/Qwen3.5-2B` | BF16 | Successors to the Qwen2.5-3B in the original plan — benchmark before committing |
| **Document layout (Arm 7)** | `PaddlePaddle/PaddleOCR-VL-1.6` | BF16 | **0.9B.** See below. |

Note the shape of that table: the biggest model in the pipeline has 3B active parameters.
On a 273 GB/s box, MoE with a small active set is not a preference, it's the architecture.

---

## Launch commands

### Proposer — Qwen3.6-35B-A3B NVFP4 (DGX Spark)

Straight from the recipe's DGX Spark section:

```bash
# container: vllm/vllm-openai:v0.24.0-ubuntu2404
vllm serve nvidia/Qwen3.6-35B-A3B-NVFP4 \
  --trust-remote-code \
  --kv-cache-dtype fp8 \
  --moe-backend marlin \
  --gpu-memory-utilization 0.5 \
  --max-model-len 262144 \
  --max-num-seqs 8 \
  --max-num-batched-tokens 8192 \
  --enable-chunked-prefill \
  --async-scheduling \
  --enable-prefix-caching \
  --speculative-config '{"method":"mtp","num_speculative_tokens":3,"moe_backend":"triton"}' \
  --load-format fastsafetensors \
  --reasoning-parser qwen3 \
  --tool-call-parser qwen3_coder \
  --enable-auto-tool-choice
```

Two flags deserve attention because they contradict guidance elsewhere:

- **`--gpu-memory-utilization 0.5`** — the Spark recipe uses 0.5 where the RTX Pro 6000
  recipe uses 0.9 for the same model. That's stricter than the 0.80 cap in `INFRA.md`,
  and it lines up with the hard-lock reports. Use 0.5 as the starting point and raise it
  deliberately, one step at a time, with `make gpu` open.
- **`--max-num-seqs 8`** — see the batching note below.

### Alt proposer — Nemotron-3-Nano-30B-A3B

```bash
# container: nvcr.io/nvidia/vllm:25.12.post1-py3
wget https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16/resolve/main/nano_v3_reasoning_parser.py

vllm serve nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16 \
  --max-num-seqs 8 \
  --tensor-parallel-size 1 \
  --max-model-len 262144 \
  --trust-remote-code \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --reasoning-parser-plugin nano_v3_reasoning_parser.py \
  --reasoning-parser nano_v3
```

Recipe notes worth keeping: `--async-scheduling` cuts host overhead between decode steps;
`--mamba-ssm-cache-dtype float32` for accuracy, `float16` for speed; `--kv-cache-dtype
fp8` only with an FP8 checkpoint.

### Document layout — PaddleOCR-VL-1.6

```bash
vllm serve PaddlePaddle/PaddleOCR-VL-1.6 \
  --trust-remote-code \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0
```

Prefix caching is disabled deliberately — OCR workloads don't reuse prefixes, so the
hashing is pure overhead.

---

## The batching nuance (correction to INFRA.md)

`INFRA.md` says "batch hard, concurrency is worth 50–100x." That came from an 8B at
concurrency 128 and a 30B-A3B at batch 64. But every Spark recipe here sets
`--max-num-seqs 8`.

Both are true, and the reconciliation matters:

- Those throughput figures used **short contexts**. These recipes default to **256K**
  context, where KV cache per sequence is enormous and 8 concurrent sequences is what
  fits in the memory budget.
- **Your context is short.** Log lines are tiny; document chunks are a few thousand
  tokens. Set `--max-model-len` to what you actually need — 8K, not 262144 — and the KV
  budget frees up enough to raise `--max-num-seqs` substantially.

So the operative rule is: **cut `--max-model-len` to your real workload first, then raise
`--max-num-seqs` until throughput stops improving or memory gets tight.** That sweep *is*
the day-1 concurrency benchmark in the bootstrap checklist, and it is the single highest-
value hour on this machine. Don't inherit 262144 from a recipe you copied.

---

## Documents: PaddleOCR-VL replaces the Docling stage

`ARCHITECTURE.md` has Docling/Marker/olmOCR converting PDFs to clean text before the
small model sees anything. The structure is right; the component changes.

PaddleOCR-VL-1.6 is **0.9B parameters**, claims 96.33% on OmniDocBench v1.6, and serves
through the same vLLM endpoint as everything else. That's a meaningfully better fit than
bolting on a separate toolchain: one server, one API, one set of ops.

It also exposes task-specific prompts — `OCR:`, `Table Recognition:`, `Formula
Recognition:`, `Chart Recognition:`, `Spotting:`, `Seal Recognition:` — which map
directly onto the document-parsing arm. Table recognition in particular is the part that
a text-only fine-tune cannot learn, and it's the part that breaks naive PDF pipelines.

Alternatives in the same catalogue worth a bake-off: `deepseek-ai/DeepSeek-OCR-2`,
`zai-org/GLM-OCR`, `tencent/HunyuanOCR`, `baidu/Unlimited-OCR`.

**The architectural rule is unchanged:** the small specialist never sees raw PDF bytes.
OCR-VL produces structured text and layout; your specialist extracts fields from that.

---

## Risks, honestly

1. **Mamba layers on sm_121 have a crash history.** vLLM issue #37431 reports Mamba-2
   Triton kernels hitting illegal-instruction errors on SM121. Nemotron-3-Nano is
   Mamba-hybrid, and Qwen3.6's gated-delta-network MoE also has Mamba-adjacent cache
   paths — the recipe's own troubleshooting mentions a Mamba cache-size error fixed by
   reducing `--max-cudagraph-capture-size`. **Smoke-test generation for a few hundred
   tokens before trusting any of these**, and prefer the model that survives.
2. **NVFP4 on sm_121 has had bugs** — earlier reports of garbage output traced to
   quantisation path issues. Verify output quality, not just that the server starts.
3. **Recipe flags are image-specific.** They were validated against one container tag.
   Pin the digest; revalidate when you move.
4. **These models postdate my reliable knowledge.** I'm reading the catalogue, not
   reporting experience with them. Benchmark against your gold set rather than trusting
   any leaderboard number, including the OmniDocBench figure above.

---

## Fallback and verification

Keep Ollama installed. If vLLM fights you for more than an afternoon, run the loop on
Ollama and revisit — the project's conclusions do not depend on the serving stack, only
on throughput. Losing a week to container debugging would be the actual failure.

Bring-up order, one step at a time:

```
□ Pull the pinned container; nvidia-smi inside it; confirm compute capability 12.1
□ Serve the smallest model first (Qwen3.5-2B) — proves the whole path end to end
□ Generate 500 tokens; read them. Checking for garbage, not just HTTP 200.
□ Add the 35B NVFP4 at --gpu-memory-utilization 0.5, --max-model-len 8192
□ Sweep --max-num-seqs: 8 → 16 → 32 → 64, recording tok/s and peak memory
□ Point LiteLLM at it; confirm the laptop can reach it through the gateway
□ Only then wire an arm to it
```

Record the sweep in `reports/serving_bench.md`. Every batch parameter in the project
comes from that table.

---

## Sources

`recipes.vllm.ai` — browse index, and the recipes for
`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16`, `Qwen/Qwen3.6-35B-A3B`, and
`PaddlePaddle/PaddleOCR-VL-1.6`. sm_121 issue history: vLLM issues #31128, #36821,
#37431. Full recipe list as JSON: `https://recipes.vllm.ai/models.json` — worth pulling
periodically, since the catalogue changes faster than any doc.
