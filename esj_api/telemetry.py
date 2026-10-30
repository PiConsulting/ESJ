"""Logs nativos de Functions y spans manuales, sin autoinstrumentación de datos."""

import json
import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from uuid import uuid4

from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
from opentelemetry.trace import SpanKind, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_request = ContextVar("request_telemetry", default=None)
_propagator = TraceContextTextMapPropagator()


def build_trace_provider() -> TracerProvider:
    provider = TracerProvider(
        resource=Resource.create({"service.name": os.getenv("WEBSITE_SITE_NAME", "esj-api")}),
        sampler=ParentBased(ALWAYS_ON),
    )
    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if connection_string:
        # Sólo trazas: el host ya exporta requests y logs. No configurar otro
        # logger/exportador ni instrumentar requests (capturaría URLs y errores).
        exporter = AzureMonitorTraceExporter(
            connection_string=connection_string,
            disable_offline_storage=True,
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


# Una instancia por worker; sin connection string no hay tráfico de telemetría.
# No sustituimos el proveedor global que otras librerías puedan registrar.
provider = build_trace_provider()
tracer = provider.get_tracer("esj_api")


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields) -> None:
    """Los callers sólo pasan metadatos conocidos; nunca req, headers o payloads."""
    span = trace.get_current_span().get_span_context()
    record = {
        "event": event,
        **(_request.get() or {}),
        "trace_id": format(span.trace_id, "032x") if span.is_valid else None,
        "span_id": format(span.span_id, "016x") if span.is_valid else None,
        **fields,
    }
    # JSON en message es portable al worker Python (no depende de custom_dimensions).
    logger.log(level, json.dumps(record, ensure_ascii=False, separators=(",", ":")))


@contextmanager
def request_scope(req, context=None):
    request_id = str(uuid4())
    invocation_id = getattr(context, "invocation_id", None)
    host_context = getattr(context, "trace_context", None)
    carrier = {
        "traceparent": getattr(host_context, "Traceparent", None) or req.headers.get("traceparent", ""),
        "tracestate": getattr(host_context, "Tracestate", None) or "",
    }
    state = _request.set({"request_id": request_id, "invocation_id": invocation_id})
    try:
        with tracer.start_as_current_span(
            "esj.request", context=_propagator.extract(carrier),
            kind=SpanKind.INTERNAL, record_exception=False, set_status_on_exception=False,
            attributes={"app.request_id": request_id, "app.invocation_id": invocation_id or "local"},
        ) as span:
            yield request_id, format(span.get_span_context().trace_id, "032x"), span
    finally:
        _request.reset(state)


@contextmanager
def operation(logger: logging.Logger, name: str, kind=SpanKind.INTERNAL):
    started = perf_counter()
    with tracer.start_as_current_span(
        name, kind=kind, record_exception=False, set_status_on_exception=False,
        attributes={"app.request_id": (_request.get() or {}).get("request_id", ""), "app.operation": name},
    ) as span:
        log_event(logger, "operation.started", operation=name)
        try:
            yield span
        except Exception as exc:
            span.set_status(StatusCode.ERROR)
            span.set_attribute("error.type", type(exc).__name__)
            log_event(logger, "operation.failed", logging.ERROR, operation=name,
                      duration_ms=round((perf_counter() - started) * 1000, 2),
                      error_type=type(exc).__name__)
            raise
        else:
            span.set_status(StatusCode.OK)
            log_event(logger, "operation.completed", operation=name,
                      duration_ms=round((perf_counter() - started) * 1000, 2))


def trace_headers() -> dict[str, str]:
    headers = {}
    _propagator.inject(headers)
    return headers
