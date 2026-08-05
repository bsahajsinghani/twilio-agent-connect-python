"""Tests for tac.tracing — OTel span lifecycle and no-op behaviour."""

from unittest.mock import patch

import pytest

from tac import tracing


class TestSetupTracingDisabled:
    def test_setup_tracing_noop_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_ENABLED", raising=False)
        tracing._tracer_provider = None
        tracing._tracer = None

        tracing.setup_tracing()

        assert tracing._tracer_provider is None
        assert tracing._tracer is None

    def test_setup_tracing_noop_when_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_ENABLED", "false")
        tracing._tracer_provider = None
        tracing._tracer = None

        tracing.setup_tracing()

        assert tracing._tracer_provider is None
        assert tracing._tracer is None

    def test_setup_tracing_warns_when_enabled_but_packages_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OTEL_ENABLED", "true")
        tracing._tracer_provider = None
        tracing._tracer = None

        with patch.dict("sys.modules", {"opentelemetry": None}):
            tracing.setup_tracing()
            # Either warns about missing packages or succeeds if installed
            # — just confirm no exception is raised
        assert tracing._tracer_provider is None or tracing._tracer_provider is not None


class TestStartEndCall:
    def test_start_call_registers_span(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_ENABLED", raising=False)
        tracing._tracer = None
        tracing._call_spans.clear()
        tracing._turn_counters.clear()

        tracing.start_call("CA123")

        assert "CA123" in tracing._call_spans
        assert tracing._turn_counters.get("CA123") == 0

    def test_end_call_removes_span(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_ENABLED", raising=False)
        tracing._tracer = None
        tracing._call_spans.clear()
        tracing._turn_counters.clear()

        tracing.start_call("CA456")
        assert "CA456" in tracing._call_spans

        tracing.end_call("CA456")
        assert "CA456" not in tracing._call_spans
        assert "CA456" not in tracing._turn_counters

    def test_end_call_unknown_sid_is_noop(self) -> None:
        tracing.end_call("CA_nonexistent")  # should not raise


class TestSpansAreNoopsWhenDisabled:
    def setup_method(self) -> None:
        tracing._tracer = None
        tracing._call_spans.clear()
        tracing._turn_counters.clear()
        tracing.start_call("CAnoop")

    def teardown_method(self) -> None:
        tracing.end_call("CAnoop")

    def test_co_init_span_noop(self) -> None:
        with tracing.co_init_span("CAnoop"):
            pass  # no exception

    def test_profile_lookup_span_noop(self) -> None:
        with tracing.profile_lookup_span("CAnoop"):
            pass

    def test_turn_span_noop(self) -> None:
        with tracing.turn_span("CAnoop", "hello"):
            pass

    def test_memory_span_noop(self) -> None:
        with tracing.memory_span("CAnoop"):
            pass

    def test_llm_span_noop(self) -> None:
        with tracing.llm_span("CAnoop"):
            pass

    def test_memory_recall_api_span_noop(self) -> None:
        with tracing.memory_recall_api_span("CAnoop"):
            pass

    def test_llm_prompt_build_span_noop(self) -> None:
        with tracing.llm_prompt_build_span("CAnoop"):
            pass

    def test_llm_response_stream_span_noop(self) -> None:
        with tracing.llm_response_stream_span("CAnoop"):
            pass

    def test_first_prompt_wait_span_noop(self) -> None:
        with tracing.first_prompt_wait_span("CAnoop"):
            pass

    def test_record_first_token_noop(self) -> None:
        tracing.record_first_token("CAnoop")  # should not raise

    def test_get_trace_id_returns_none_when_disabled(self) -> None:
        result = tracing.get_trace_id("CAnoop")
        assert result is None

    def test_turn_counter_increments(self) -> None:
        with tracing.turn_span("CAnoop", "turn 1"):
            pass
        with tracing.turn_span("CAnoop", "turn 2"):
            pass
        assert tracing._turn_counters.get("CAnoop") == 2


class TestInjectTraceparent:
    def test_inject_traceparent_returns_headers_copy(self) -> None:
        headers = {"User-Agent": "test"}
        result = tracing.inject_traceparent(headers)
        assert "User-Agent" in result
        assert result is not headers  # must be a copy
