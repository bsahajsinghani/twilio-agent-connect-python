"""OpenTelemetry tracing setup for TAC.

Exports traces to the Twilio internal OTel gateway.
Configure via environment variables:
    OTEL_ENABLED=true           (default: false)
    OTEL_ENDPOINT               (default: https://otelgw-pub0.us-east-1.dev.platform.twilioinfra.com)
    OTEL_SERVICE_NAME           (default: tac-voice-agent)
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

_DEFAULT_ENDPOINT = "https://otelgw-pub0.us-east-1.dev.platform.twilioinfra.com"
_DEFAULT_SERVICE_NAME = "tac-voice-agent"

_tracer_provider: Any = None
_tracer: Any = None

# Root spans keyed by call_sid — kept alive for the duration of the call.
_call_spans: dict[str, Any] = {}
# Turn counters per call_sid.
_turn_counters: dict[str, int] = {}


def setup_tracing() -> None:
    """Initialize OTel tracing if OTEL_ENABLED=true."""
    global _tracer_provider, _tracer

    if os.environ.get("OTEL_ENABLED", "").lower() != "true":
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

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
    from opentelemetry.trace import NonRecordingSpan, use_span

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


def inject_traceparent(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of headers with W3C traceparent injected from the current span context."""
    from opentelemetry.propagate import inject

    carrier: dict[str, str] = dict(headers)
    inject(carrier)
    return carrier


def shutdown_tracing() -> None:
    """Flush and shut down the tracer provider."""
    if _tracer_provider is not None:
        _tracer_provider.shutdown()
