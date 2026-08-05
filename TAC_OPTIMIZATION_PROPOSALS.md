# TAC Optimization Proposals

**Author:** Bhoomi Sahajsinghani | **Status:** Proposal | **Context:** Voice agent latency benchmarking on TAC

---

## Progress Checklist

| # | Optimization | Status | PR / Notes |
|---|---|---|---|
| 1 | OTel tracing layer | Done | In `src/tac/tracing.py` — auto-init wired, default endpoint updated |
| 2 | STT provider + model as first-class config | Done | `transcription_provider` + `speech_model` in `VoiceChannelConfig` |
| 3 | `list_all` memory mode / List Observations API | Done | [PR #98](https://github.com/twilio/twilio-agent-connect-python/pull/98) |
| 4 | Pluggable LLM provider interface | Proposed | |
| 5 | Raise memory limit cap (100 → 500) | Done | Validator updated |
| 6 | co_init latency warning | Proposed | |
| 7 | Freeze observation snapshot via `createdBefore` | Proposed | |
| 8 | `call_sid` in session metadata | Done | `ConversationSession.metadata["call_sid"]` |
| 9 | Early co_init (fire init task on setup_msg) | Validated | Experiment 9h — 80% TTFA reduction. Pending core PR. |
| 10 | Parallel co_init + profile lookup | Validated | Experiment 9i — profile_lookup 753ms → 390ms. Pending core PR. |
| 11 | Redis-backed conversation store | Proposed | |
| 12 | Built-in experiment harness (`tac[bench]`) | In progress | `experiment.py` + `jaeger.py` built in caller-agent |
| — | Pre-fetch observations in init_task | In progress | Experiment 9j — running now |
| — | TLS connection pooling in `BaseAPIClient` | Proposed | D1 — reuse `httpx.AsyncClient`, ~285ms saving per API call |

---

## 1. OTel Tracing for End-to-End Latency Visibility

**Problem:** No built-in observability into where latency comes from in a TAC voice call. Developers have no way to distinguish CO polling delay, profile lookup, memory retrieval, or LLM TTFT without external tooling.

**Change:** Add `tracing.py` to TAC core with spans for: `call.co_init` (CO polling), `call.profile_lookup`, `memory.recall`, `llm.completion`, `llm.first_token_sent`, `llm.response_stream`. Exports to any OTel-compatible backend (Jaeger, Grafana, Twilio OTel gateway) via `OTEL_ENABLED=true`.

**Why it matters:** `co_init` alone adds 450–1800ms depending on CO warm/cold state — invisible without tracing. Any TAC user debugging slow first responses needs this.

---

## 2. STT Provider + Model as First-Class Config

**Problem:** ConversationRelay supports multiple STT providers (Deepgram, Google) and models (Flux, Nova-3), but TAC had no way to configure them — hardcoded defaults only.

**Change:** Add `transcription_provider` and `speech_model` fields to `VoiceChannelConfig` and `TwimlOptions`. Passed through to ConversationRelay's `transcriptionProvider` and `speechModel` attributes at call setup.

**Why it matters:** Deepgram Flux reduces STT latency significantly vs defaults. Any user optimizing for voice latency needs this without patching TAC internals.

---

## 3. `list_all` as a First-Class Voice Memory Mode

**Problem:** TAC ships with the Recall API as the default memory retrieval path. The List Observations API (`GET /v1/Profiles/{id}/Observations`) exists but is undocumented in TAC — users have no way to discover it or know when to prefer it. Recall API is capped at 100 observations, re-fetches semantically every turn adding ~750ms per turn, and prevents prefix caching. The optimal voice pattern — fetch all obs once at call start, inject as a static block — requires bypassing TAC's memory system entirely and implementing it manually.

**Change:** Add `list_all` as a named `memory_mode` in `VoiceChannelConfig`. TAC fetches all observations via the List Observations API at call start, injects them as a static block in the system prompt, and caches them for the duration of the call. Document when to use Recall (dynamic, semantic, low obs count) vs `list_all` (static block, voice, caching).

**Why it matters:** Switching from Recall to `list_all` eliminates ~750ms per-turn recall latency and enables prefix caching. Our benchmarking shows `list_all` + cache at 500 obs outperforms recall-every-turn by ~750ms per turn. Without this mode, every TAC voice developer has to rediscover this pattern manually.

---

## 4. Pluggable LLM Provider Interface

**Problem:** TAC examples hardwire the OpenAI Agents SDK. Swapping to SageMaker or Bedrock requires writing an entirely new application script — there is no abstraction for the LLM layer in TAC itself.

**Change:** Introduce a thin `LLMProvider` protocol in TAC with implementations for OpenAI, SageMaker (boto3 + async queue bridge), and Bedrock. Application code registers a provider; TAC handles streaming, first-token events, and OTel tracing uniformly regardless of backend.

**Why it matters:** Our SageMaker experiments required a 390-line script duplicating all TAC plumbing. A provider abstraction reduces this to a config change and unblocks multi-provider A/B testing within a single TAC deployment. SageMaker Qwen2.5-1.5B shows 44% lower LLM TTFT vs gpt-4o-mini with caching — model portability is critical for latency-sensitive voice deployments.

---

## 5. Raise Memory Limit Cap (100 → 500)

**Problem:** `TwilioMemoryConfig` and `MemoryRetrievalRequest` validators capped `observations_limit`, `summaries_limit`, `communications_limit` at 100, incorrectly blocking the List Observations API which supports up to 1000.

**Change:** Raise `le=100` to `le=500` on all three fields.

**Why it matters:** Simple validator fix. Without it, any user trying to fetch more than 100 observations gets a silent validation error.

---

## 6. co_init Latency Warning

**Problem:** The CO polling loop (`_initialize_conversation`) silently adds 450–1800ms at call start depending on Conversation Orchestrator load. Developers have no signal that this is happening.

**Change:** Add a structured log warning when `co_init` exceeds a threshold (e.g. 800ms): `"co_init exceeded threshold, CO may be under load"`. Already instrumented via OTel span — warning adds an additional actionable signal without tracing setup.

**Why it matters:** In our experiments, cold co_init (1500–1800ms) inflated TTFA by 2×, making it impossible to interpret latency results without this signal.

---

## 7. Freeze Observation Snapshot via `createdBefore`

**Problem:** Conversation Intelligence continuously aggregates new observations into a profile. Between experiment runs (or between calls), the observation set grows — making cache vs no-cache comparisons inconsistent since the prompt size changes.

**Change:** Add optional `observations_created_before` parameter to the List Observations API call. Passed as `createdBefore` query param to freeze the snapshot to a specific timestamp.

**Why it matters:** Ensures deterministic memory across calls in production (e.g. snapshot at session start) and makes A/B testing reproducible.

---

## 8. call_sid in Session Metadata

**Problem:** Application callbacks (`on_message_ready`) receive `ConversationSession` but not the Twilio call SID — forcing developers to thread it separately if they need to tag LLM calls, logs, or traces.

**Change:** Store `call_sid` in `ConversationSession.metadata["call_sid"]` at call setup so it's accessible in any callback without additional plumbing.

**Why it matters:** Small but broadly useful — any voice app that wants to correlate LLM calls or logs with a specific Twilio call SID needs this.

---

## 9. Pre-warm CO Connection on WebSocket Open

**Problem:** `co_init` (Conversation Orchestrator polling) adds 450–1800ms before the first turn starts. Currently `_initialize_conversation` is called on the first `prompt` message — wasting the entire time the user spends saying their first sentence. Profile lookup (~400–700ms) runs sequentially after co_init, adding further dead time.

**Change:** Fire `_initialize_conversation` as an `asyncio.create_task()` immediately after `setup_msg` is received — before the first prompt arrives. When the first prompt fires, `await` the already-running task. If it finished during the user's speech, `waited_ms = 0`. If not, wait from that point as normal.

```
Current:
  WebSocket open → setup_msg → [user speaks] → co_init (450–1800ms) → profile_lookup (400ms) → LLM

With this change:
  WebSocket open → setup_msg → co_init starts ──────────────────┐
                                [user speaks 3–6s]              ↓
                               profile_lookup starts ────────────┤
                                                    → waited_ms=0 → LLM starts immediately
```

**Experimental proof (9h_early_coinit_test, 2 runs each):**

| Metric | Baseline | Early co_init | Δ |
|---|---|---|---|
| TTFA avg | 3,293ms | **642ms** | **−80%** |
| App Lat avg | 3,097ms | **448ms** | **−85%** |
| co_init | 894ms warm | 3,835ms **cold** | ran during speech → 0ms impact |
| profile_lookup | 600ms | 586ms | ran during speech → 0ms impact |
| waited_ms on first prompt | ~1,494ms blocked | **0.0ms** | |

Early co_init had a **3.8s cold CO start** yet achieved **642ms TTFA** — while baseline with a warm 894ms co_init hit **3,293ms TTFA**. No matter how slow co_init is, it never touches TTFA with this change.

**Implementation:** Single-file change in `src/tac/channels/voice/channel.py` — `handle_websocket`. Fire `asyncio.create_task(_initialize_conversation(...))` after setup_msg; `await` the task at the top of the first prompt handler. Cancel task in exception handler for clean error propagation. Zero application-layer changes. Prototype in `getting_started/examples/features/voice_streaming_early_coinit.py`.

**Why it matters:** Eliminates co_init + profile_lookup (~1,000–1,500ms) from the TTFA critical path on every single call. Absorbs cold starts (3,800ms+) entirely for free. Biggest single latency win available in TAC with minimal code change.

---

## 10. Parallel co_init and Profile Lookup

**Problem:** Profile lookup (~400ms) runs sequentially after co_init completes. These two operations are independent — profile lookup only needs the caller's phone number, which is available from the WebSocket handshake.

**Change:** Fire co_init and profile lookup concurrently at call start. Join both results before triggering `on_message_ready`.

**Why it matters:** In our experiments, co_init (~600ms avg warm, ~1500ms cold) + profile_lookup (~400ms) = ~1000–1900ms before the LLM call even starts. Running them in parallel cuts this to `max(co_init, profile_lookup)` — saving ~400ms on every call.

---

## 11. Redis-Backed Conversation Store

**Problem:** TAC tracks active conversations in instance-local memory (`self._conversations`). Works fine for single-instance deployments but breaks under load balancers — a webhook routed to a different instance than the one that handled the WebSocket connection causes missed cleanup and memory leaks.

**Change:** Add an optional `conversation_store` config that accepts a Redis (or compatible) backend. Conversation state is read/written through the store, making all instances share the same view.

**Why it matters:** Any production TAC deployment behind a load balancer needs this. Without it, the recommended workaround is sticky sessions — an infrastructure constraint that limits scaling options.

---

## 12. Built-in Experiment Harness (`tac[bench]`)

**Problem:** There is no standard way to benchmark a TAC voice agent. Everything needed to measure TTFA, App Lat, LLM TTFT, token usage, and cache hit rate had to be built outside TAC — a custom caller agent, Voice Insights fetcher, OTel/Jaeger querier, and results schema. Every TAC developer who wants to measure latency has to rebuild this from scratch.

**Change:** Add an optional `tac[bench]` extra that provides: outbound call orchestration, automatic Voice Insights fetching, OTel span querying (TTFT, co_init, profile_lookup), token usage logging, and a standard `results.json` schema. Configurable via a simple `experiment.yaml`.

**Why it matters:** Reproducible benchmarking is the foundation for any latency optimisation work. Without it, developers can't measure whether their changes helped — and comparisons across model providers, memory modes, or TAC versions are impossible.
