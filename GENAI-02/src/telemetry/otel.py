"""OpenTelemetry provider, traces, and low-cardinality runtime metrics."""
import logging
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

from src.config import Settings

logger = logging.getLogger(__name__)

try:
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None  # type: ignore[assignment]
    metrics = None  # type: ignore[assignment]

_initialized = False
_node_counter: Any = None
_node_duration: Any = None
_tool_counter: Any = None
_sandbox_duration: Any = None


def configure_telemetry(settings: Settings) -> None:
    global _initialized, _node_counter, _node_duration, _tool_counter, _sandbox_duration
    if _initialized or not settings.otel_enabled or not OTEL_AVAILABLE:
        return
    resource = Resource.create({"service.name": settings.otel_service_name, "deployment.environment": settings.app_env})
    trace_provider = TracerProvider(resource=resource)
    headers: dict[str, str] = {}
    if settings.otel_exporter_otlp_headers:
        for pair in settings.otel_exporter_otlp_headers.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                headers[key.strip()] = value.strip()
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
    trace_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=headers)))
    trace.set_tracer_provider(trace_provider)
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", headers=headers), export_interval_millis=15_000
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    meter = meter_provider.get_meter("nexvia.genai02")
    _node_counter = meter.create_counter("workflow.node.executions", unit="{execution}")
    _node_duration = meter.create_histogram("workflow.node.duration", unit="ms")
    _tool_counter = meter.create_counter("workflow.tool.calls", unit="{call}")
    _sandbox_duration = meter.create_histogram("workflow.sandbox.duration", unit="ms")
    _initialized = True
    logger.info("OpenTelemetry tracing and metrics initialized", extra={"tool": "otel"})


def get_tracer(name: str = "nexvia.genai02") -> Any:
    if OTEL_AVAILABLE:
        return trace.get_tracer(name)
    return None


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[None]:
    tracer = get_tracer()
    if tracer is None:
        yield
        return
    with tracer.start_as_current_span(name, attributes=attributes or {}):
        yield


def record_node(name: str, duration_ms: float, success: bool = True) -> None:
    attributes = {"node.name": name, "success": success}
    if _node_counter is not None:
        _node_counter.add(1, attributes)
    if _node_duration is not None:
        _node_duration.record(max(duration_ms, 0.0), attributes)


def record_tool(name: str, success: bool = True, duration_ms: float | None = None) -> None:
    attributes = {"tool.name": name, "success": success}
    if _tool_counter is not None:
        _tool_counter.add(1, attributes)
    if duration_ms is not None and _node_duration is not None:
        _node_duration.record(max(duration_ms, 0.0), {"node.name": f"tool:{name}", "success": success})


def record_sandbox(duration_ms: float, success: bool, mode: str) -> None:
    if _sandbox_duration is not None:
        _sandbox_duration.record(max(duration_ms, 0.0), {"sandbox.mode": mode, "success": success})

from functools import wraps
from time import perf_counter
from collections.abc import Awaitable, Callable
from typing import TypeVar

_T = TypeVar("_T")


def observe_tool(name: str) -> Callable[[Callable[..., Awaitable[_T]]], Callable[..., Awaitable[_T]]]:
    """Decorator that records duration, success and a trace span for async MCP handlers."""
    def decorate(fn: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
        @wraps(fn)
        async def wrapped(*args: Any, **kwargs: Any) -> _T:
            started = perf_counter()
            succeeded = False
            try:
                with span(name, {"mcp.tool": name}):
                    result = await fn(*args, **kwargs)
                succeeded = True
                return result
            finally:
                record_tool(name, succeeded, (perf_counter() - started) * 1000)
        return wrapped
    return decorate
