# TAC Latency Observability Plan

## Overview

End-to-end voice call latency = STT + Recall + LLM + TTS + network overhead.  
We instrument each layer differently because we control different parts of the stack.

---

## Metric Sources

### A. Memora Grafana (already live — company Grafana, no setup needed)
Covers the **inside** of a Recall call. Correlate to a TAC call via `call.sid` attribute.

| Metric | Grafana trace name | Notes |
|---|---|---|
| Embedding generation | `generate_embedding` / `embed.duration` | Time to embed the query string |
| Vector search | `opensearch_client.search` | Time inside OpenSearch |
| Total Recall round-trip (server-side) | `recall.duration` | Does not include HTTP network to/from TAC |

### B. Twilio Voice Insights API (already available — pull after each call)
`GET https://insights.twilio.com/v1/Voice/{call_sid}/Summary`  
Returns per-call averages across all turns.

| Metric | JSON field | Notes |
|---|---|---|
| STT latency | `stt_latency_ms` | Deepgram processing time |
| TTS latency | `tts_latency_ms` | TTS audio generation time |
| Time to first audio | `time_to_first_audio_ms` | TTFA — what user actually hears. **Twilio flags calls > 1200ms as poor experience.** |
| Application latency | `application_latency_ms` | Time Twilio waited for our app (Recall + LLM). app + tts ≈ TTFA. |
| Words per minute | `words_per_minute` (avg) | Agent speaking rate. Natural human speech = 120–180 wpm. |
| Tokens per call | `tokens` (total) | Total WebSocket chunks sent per call |
| Interruption rate | `interruptions` | How often user interrupted agent. High = agent too slow. Should drop as latency improves. |
| Number of turns | `turns` | |

### C. TAC Local OTel → Jaeger (need to set up)
Covers **TAC-side** timing: what happens between receiving an STT chunk and sending first audio byte.

| Metric | Span name | Status |
|---|---|---|
| Full call duration | `call` (root span) | ✅ wired in `channel.py:292,817` |
| Per-turn total | `turn` | ✅ wired in `channel.py:719` |
| Recall HTTP round-trip (TAC side) | `memory.recall` | ✅ wired in `channel.py:721` — wraps full Python call |
| LLM + response total | `llm.completion` | ✅ wired in `channel.py:728` |
| CO init / session polling | `call.co_init` | ❌ missing |
| Profile lookup | `call.profile_lookup` | ❌ missing |
| LLM time-to-first-token | `llm.ttft` sub-span | ❌ missing (need to split `llm.completion`) |
| First token sent to WebSocket | `response.first_token_sent` | ❌ missing |

### D. VAD on dual-channel recording (voice-ai-benchmark approach)
True mouth-to-ear TTFA — cannot be derived from code timestamps alone.

| Metric | Method | Notes |
|---|---|---|
| Per-turn mouth-to-ear gap | silero_vad on dual-channel WAV | voice-ai-benchmark `vad_gaps_multi()` |
| TTFA excl. LLM | `gap_ex_llm_ms` | gap minus LLM latency |

---

## Work Checklist

### Phase 1 — Local Jaeger via Docker

- [x] **1.1** Run Jaeger all-in-one container
  ```bash
  docker run -d --name jaeger \
    -p 16686:16686 \
    -p 4318:4318 \
    jaegertracing/all-in-one:latest
  ```
- [x] **1.2** Update `.env`: `OTEL_ENDPOINT=http://localhost:4318`
- [x] **1.3** Start `voice_streaming.py`, make a test call, open `http://localhost:16686` and verify spans appear
- [x] **1.4** Confirm `call → turn → memory.recall + llm.completion` hierarchy is visible

### Phase 2 — Fill in missing TAC spans

- [x] **2.1** Add `call.co_init` span — wrap the Conversation Orchestrator polling loop at call start
- [x] **2.2** Add `call.profile_lookup` span — wrap the profile resolution by phone number
- [x] **2.3** Split `llm.completion` into two sub-spans:
  - `llm.ttft` — ends when first streaming token arrives
  - `llm.generation` — ends when full response is sent to WebSocket
- [x] **2.4** Add `response.first_token_sent` event on the `turn` span (timestamp when first byte goes over WebSocket)
- [x] **2.5** Wire `inject_traceparent()` into the Recall HTTP call headers (already in `tracing.py`, just not called)
- [x] **2.6** Make a test call, verify all new spans in Jaeger

### Phase 3 — Voice Insights pull

- [ ] **3.1** Add `voice_insights.py` helper to caller-agent — fetches call summary after a call ends
  - Input: `call_sid`
  - Output: `{stt_latency_ms, tts_latency_ms, time_to_first_audio_ms, application_latency_ms, turns}`
- [ ] **3.2** Test it manually against a recent call SID
- [ ] **3.3** Integrate into the experiment runner output (Phase 4)

### Phase 4 — Experiment runner

- [ ] **4.1** Create `experiment.py` in caller-agent repo with structure:
  ```
  experiments/
    {name}/
      config.json      ← scenario, model, memory_mode, speech_model, notes
      results.json     ← per-turn metrics + averages + voice insights summary
  ```
- [ ] **4.2** `config.json` schema:
  ```json
  {
    "scenario": "knowledge_update",
    "speech_model": "flux",
    "memory_mode": "always",
    "llm_model": "gpt-4o-mini",
    "notes": "baseline with Deepgram Flux"
  }
  ```
- [ ] **4.3** `results.json` schema:
  ```json
  {
    "call_sid": "CA...",
    "config": { ... },
    "voice_insights": {
      "stt_latency_ms": 210,
      "tts_latency_ms": 180,
      "time_to_first_audio_ms": 2100,
      "application_latency_ms": 1800,
      "turns": 8
    },
    "turns": [
      {
        "turn": 1,
        "memory_fetch_ms": 165,
        "llm_ttft_ms": 480,
        "llm_total_ms": 1200,
        "first_audio_sent_ms": 1380
      }
    ],
    "averages": {
      "memory_fetch_ms": { "p50": 160, "p90": 210 },
      "llm_ttft_ms":     { "p50": 490, "p90": 750 }
    }
  }
  ```
- [ ] **4.4** `experiment.py` CLI flow:
  1. Load `config.json`
  2. Call `caller.py` logic to place call
  3. Wait for call to end (poll call status)
  4. Fetch Voice Insights summary
  5. Write `results.json`
- [ ] **4.5** Test end-to-end with `knowledge_update` scenario

### Phase 5 — VAD-based TTFA (stretch)

- [ ] **5.1** Add Twilio `<Record>` with dual-channel to TwiML in `twiml.py`
- [ ] **5.2** Download recording WAV after call ends
- [ ] **5.3** Run `vad_gaps_multi()` from voice-ai-benchmark on the WAV
- [ ] **5.4** Add `gap_ms` and `gap_ex_llm_ms` per turn to `results.json`

---

## Industry Latency Standards for Voice AI

Referenced from Twilio's own ConversationRelay Insights dashboard and voice AI research:

| Metric | Good | Acceptable | Poor |
|---|---|---|---|
| TTFA (`time_to_first_audio_ms`) | < 800ms | < 1200ms | > 1200ms ⚠️ Twilio flags these |
| TTFT (`first_token_sent`) | < 500ms | < 800ms | > 1000ms |
| Application latency | < 600ms | < 1000ms | > 1500ms |
| Agent speaking rate | 130–160 wpm | 120–180 wpm | < 100 or > 200 wpm |
| Interruption rate | < 10% of turns | < 20% | > 30% (users giving up) |

> Current baseline (from first traces): TTFA ~3924ms, application latency ~3707ms — well above acceptable thresholds. Primary bottleneck is LLM. Secondary bottleneck is Recall TLS overhead.

---

## Experiment Configurations (Proposal Phases 1 & 2)

Run each with the same scenario (`knowledge_update`) and same call count (3 runs, take median).

### Phase 1 — Baseline & Traditional Recall

| Experiment | `memory_mode` | Notes |
|---|---|---|
| `1a_no_memory` | `"never"` | Pure speed baseline. No memory at all. |
| `1b_recall_always` | `"always"` | Semantic recall on every utterance (current default). |
| `1c_recall_tool_call` | LLM tool | LLM decides when to call Recall. (requires TAC tool wiring) |

### Phase 2 — Startup Memory Ingestion

| Experiment | `memory_mode` | Memory limit | Notes |
|---|---|---|---|
| `2a_recall_once` | `"once"` | unlimited | Fetch all memories at call start, zero recalls after. |
| `2b_top_10` | `"once"` | 10 | Fixed count sweep — does memory count affect latency? |
| `2b_top_20` | `"once"` | 20 | |
| `2b_top_50` | `"once"` | 50 | |
| `2b_top_100` | `"once"` | 100 | |
| `2b_top_200` | `"once"` | 200 | |
| `2b_top_500` | `"once"` | 500 | Expected: latency increases as prompt grows → LLM context overhead |

---

## Going Deeper — Intern Differentiators

These go beyond the proposal. Each one is a concrete optimization with a before/after measurement.

| # | What | Expected impact | Status |
|---|---|---|---|
| D1 | **Fix TLS connection pooling in `BaseAPIClient`** — reuse httpx `AsyncClient` instead of creating new one per request | Drop per-recall from ~450ms → ~165ms | ❌ not done |
| D2 | **`memory_mode: "once"` + connection pooling combined** | Recall cost turns 2+ → ~0ms | ❌ not done |
| D3 | **STT model comparison** — nova-2 vs nova-3 vs flux on same scenario | Chunk:turn ratio + STT latency impact | ✅ flux wired, comparison not run yet |
| D4 | **Prompt caching** — restructure system prompt so static content (instructions + memories loaded at startup) is always the prefix, dynamic content (conversation history) always appended after. OpenAI caches the prefix automatically. | Reduce TPOT (time per output token), improve throughput on long calls | ❌ not done |
| D5 | **Interruption rate as a proxy metric** — track how interruption rate changes across memory strategies. Lower latency → fewer interruptions → better UX | Qualitative proof that latency improvements matter | ❌ not tracked yet |

---

## STT Model Comparison

Run same scenario 3× per model:

| Model | `stt_latency_ms` | `application_latency_ms` | `time_to_first_audio_ms` | interruptions | STT chunks/turn |
|---|---|---|---|---|---|
| nova-2 (default) | | | | | |
| nova-3 | | | | | |
| flux (turn-aware) | | | | | |

---

## Execution order

Infra (Phases 1–3 done) → Experiment runner (Phase 4) → Run experiments (Proposal Phase 1 → 2) → Differentiators (D1–D5) → Results + blog post

---

## Insights Log

> [!NOTE]
> **CO init (~535ms) + profile lookup (~457ms) = ~1s overhead at call start** — unavoidable (CO startup time), but now visible in Jaeger.

> [!WARNING]
> **`memory.recall` TAC-side is 686ms–1.18s per turn; Memora server-side is ~165ms** — the gap (~500–1000ms) is network round-trip through ngrok. Switch `memory_mode` from `"always"` to `"once"` to eliminate recall on turns 2+ with no quality loss (memories don't update mid-call). Expected saving: ~700ms/turn.

> [!NOTE]
> **The overhead is not a region mismatch — TAC and Memora are both in `us-east-1`, same AWS building (intra-AZ).** The extra ~280ms per recall is TLS: every time TAC calls Memora it creates a brand-new encrypted connection from scratch, paying the encryption setup cost before any real work starts. If the HTTP client were reused across calls (connection pooling), TLS is paid once and each recall would drop from ~450ms to ~165ms. Combined with `memory_mode: "once"`, per-turn recall overhead goes from ~700ms to near zero after turn 1.
