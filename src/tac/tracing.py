"""OpenTelemetry tracing setup for TAC.

Exports traces to any OTel-compatible backend (Jaeger, Grafana Tempo, Honeycomb, Datadog).
Requires the tac[tracing] optional extra: pip install tac[tracing]

Configure via environment variables:
    OTEL_ENABLED=true           (default: false)
    OTEL_ENDPOINT               (default: http://localhost:4318)
    OTEL_SERVICE_NAME           (default: tac-voice-agent)
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_DEFAULT_ENDPOINT = "http://localhost:4318"
_DEFAULT_SERVICE_NAME = "tac-voice-agent"

_tracer_provider: Any = None
_tracer: Any = None

# Root spans keyed by call_sid — kept alive for the duration of the call.
_call_spans: dict[str, Any] = {}
# Turn counters per call_sid.
_turn_counters: dict[str, int] = {}


def setup_tracing() -> None:
    """Initialize OTel tracing if OTEL_ENABLED=true.

    Requires the tac[tracing] optional extra to be installed.
    If OTEL_ENABLED is not set or the packages are not installed, this is a no-op.
    """
    global _tracer_provider, _tracer

    if os.environ.get("OTEL_ENABLED", "").lower() != "true":
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        import logging
        logging.getLogger(__name__).warning(
            "OTEL_ENABLED=true but opentelemetry packages are not installed. "
            "Run: pip install tac[tracing]"
        )
        return

    endpoint = os.environ.get("OTEL_ENDPOINT", _DEFAULT_ENDPOINT)
    service_name = os.environ.get("OTEL_SERVICE_NAME", _DEFAULT_SERVICE_NAME)

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    _tracer_provider = provider
    _tracer = trace.get_tracer("tac")


def _get_tracer() -> Any:
    from opentelemetry import trace

    if _tracer is not None:
        return _tracer
    return trace.get_tracer("tac")


def start_call(call_sid: str) -> None:
    """Start a root span for the call. Must be paired with end_call()."""
    tracer = _get_tracer()
    span = tracer.start_span("call", attributes={"call.sid": call_sid})
    _call_spans[call_sid] = span
    _turn_counters[call_sid] = 0


def end_call(call_sid: str) -> None:
    """End and export the root span for the call."""
    span = _call_spans.pop(call_sid, None)
    _turn_counters.pop(call_sid, None)
    if span is not None:
        span.end()


def _get_call_context(call_sid: str) -> Any:
    """Return an OTel context containing the root span, or None."""
    from opentelemetry.trace import use_span

    span = _call_spans.get(call_sid)
    if span is None:
        return None
    return use_span(span, end_on_exit=False)


@contextmanager
def turn_span(call_sid: str, utterance: str) -> Iterator[Any]:
    """Child span for one conversational turn under the call root span."""
    from opentelemetry import context, trace

    _turn_counters[call_sid] = _turn_counters.get(call_sid, 0) + 1
    turn_num = _turn_counters[call_sid]

    root_span = _call_spans.get(call_sid)
    ctx = trace.set_span_in_context(root_span) if root_span is not None else context.get_current()

    tracer = _get_tracer()
    with tracer.start_as_current_span(
        "turn",
        context=ctx,
        attributes={
            "call.sid": call_sid,
            "turn.number": turn_num,
            "turn.utterance": utterance[:200],
        },
    ) as span:
        yield span


@contextmanager
def co_init_span(call_sid: str) -> Iterator[Any]:
    """Child span wrapping the CO polling loop at call start.

    This covers the time TAC spends waiting for Conversation Orchestrator to
    create the conversation after ConversationRelay connects. Can be 200-800ms
    depending on CO load.
    """
    from opentelemetry import context, trace

    root_span = _call_spans.get(call_sid)
    ctx = trace.set_span_in_context(root_span) if root_span is not None else context.get_current()
    tracer = _get_tracer()
    with tracer.start_as_current_span(
        "call.co_init",
        context=ctx,
        attributes={"call.sid": call_sid},
    ) as span:
        yield span


@contextmanager
def profile_lookup_span(call_sid: str) -> Iterator[Any]:
    """Child span wrapping the participant fetch + profile resolution.

    Covers list_participants() + resolving the customer phone number to a
    profile ID. Runs immediately after co_init.
    """
    from opentelemetry import context, trace

    root_span = _call_spans.get(call_sid)
    ctx = trace.set_span_in_context(root_span) if root_span is not None else context.get_current()
    tracer = _get_tracer()
    with tracer.start_as_current_span(
        "call.profile_lookup",
        context=ctx,
        attributes={"call.sid": call_sid},
    ) as span:
        yield span


@contextmanager
def memory_span(call_sid: str) -> Iterator[Any]:
    """Child span wrapping the Memora Recall HTTP call."""
    tracer = _get_tracer()
    with tracer.start_as_current_span(
        "memory.recall",
        attributes={"call.sid": call_sid},
    ) as span:
        yield span


@contextmanager
def llm_span(call_sid: str) -> Iterator[Any]:
    """Child span wrapping the LLM call + response send."""
    tracer = _get_tracer()
    with tracer.start_as_current_span(
        "llm.completion",
        attributes={"call.sid": call_sid},
    ) as span:
        yield span


@contextmanager
def memory_profile_lookup_span(call_sid: str) -> Iterator[Any]:
    """Sub-span under memory.recall — wraps lookup_profile() HTTP call.

    Runs when profile_id is not set and TAC needs to resolve it from the
    caller's phone number via the Memory API.
    """
    tracer = _get_tracer()
    with tracer.start_as_current_span(
        "memory.profile_lookup",
        attributes={"call.sid": call_sid},
    ) as span:
        yield span


@contextmanager
def memory_profile_fetch_span(call_sid: str) -> Iterator[Any]:
    """Sub-span under memory.recall — wraps get_profile() HTTP call.

    Fetches full profile traits (name, contact info, etc.) from the Memory API
    once the profile_id is known.
    """
    tracer = _get_tracer()
    with tracer.start_as_current_span(
        "memory.profile_fetch",
        attributes={"call.sid": call_sid},
    ) as span:
        yield span


@contextmanager
def memory_recall_api_span(call_sid: str) -> Iterator[Any]:
    """Sub-span under memory.recall — wraps the actual retrieve_memory() HTTP call.

    This is the Memora vector search call — embedding + OpenSearch query.
    Should match the recall.duration seen on Memora's Grafana (~165ms server-side).
    """
    tracer = _get_tracer()
    with tracer.start_as_current_span(
        "memory.recall_api",
        attributes={"call.sid": call_sid},
    ) as span:
        yield span


@contextmanager
def llm_prompt_build_span(call_sid: str) -> Iterator[Any]:
    """Sub-span under llm.completion — wraps prompt construction + memory injection.

    Covers MemoryPromptBuilder.build/compose() and deepcopy of messages.
    Usually small (<10ms) but confirms no surprise overhead.
    """
    tracer = _get_tracer()
    with tracer.start_as_current_span(
        "llm.prompt_build",
        attributes={"call.sid": call_sid},
    ) as span:
        yield span


@contextmanager
def llm_response_stream_span(call_sid: str) -> Iterator[Any]:
    """Sub-span under llm.completion — wraps the WebSocket streaming loop.

    Starts when first token is sent to Twilio, ends when last token is sent.
    Duration = how long the agent was actively speaking (streaming tokens).
    """
    tracer = _get_tracer()
    with tracer.start_as_current_span(
        "llm.response_stream",
        attributes={"call.sid": call_sid},
    ) as span:
        yield span


def record_first_token(call_sid: str) -> None:
    """Add a 'first_token_sent' event to the currently active span.

    Call this the moment the first streaming token is written to the WebSocket.
    Shows as a point-in-time marker on the llm.completion span in Jaeger,
    splitting it visually into 'waiting for LLM' vs 'streaming tokens out'.
    """
    from opentelemetry import trace

    span = trace.get_current_span()
    span.add_event("first_token_sent", {"call.sid": call_sid})


def get_trace_id(call_sid: str) -> str | None:
    """Return the hex trace ID for a call's root span, or None if not found.

    Useful for logging the Jaeger trace ID alongside the call SID so results
    can be cross-referenced with the Jaeger UI.
    """
    span = _call_spans.get(call_sid)
    if span is None:
        return None
    ctx = span.get_span_context()
    if ctx is None or not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x")


def inject_traceparent(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of headers with W3C traceparent injected from the current span context."""
    from opentelemetry.propagate import inject

    carrier: dict[str, str] = dict(headers)
    inject(carrier)
    return carrier


@contextmanager
def first_prompt_wait_span(call_sid: str) -> Iterator[Any]:
    """Child span wrapping the await of init_task on the first prompt.

    Measures how long the first prompt was blocked waiting for _initialize_conversation
    to complete. Ideally 0ms — init_task finished during user speech. Non-zero means
    co_init outlasted user speech and the first response was delayed.
    """
    from opentelemetry import context, trace

    root_span = _call_spans.get(call_sid)
    ctx = trace.set_span_in_context(root_span) if root_span is not None else context.get_current()
    tracer = _get_tracer()
    with tracer.start_as_current_span(
        "call.first_prompt_wait",
        context=ctx,
        attributes={"call.sid": call_sid},
    ) as span:
        yield span


def shutdown_tracing() -> None:
    """Flush and shut down the tracer provider."""
    if _tracer_provider is not None:
        _tracer_provider.shutdown()
