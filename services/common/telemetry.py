"""OpenTelemetry tracing setup shared by all Python services.

Spans are exported over OTLP/HTTP to Grafana Alloy, which forwards them to
Tempo. Auto-instrumentation covers FastAPI (server spans), httpx (client
spans + W3C `traceparent` propagation), psycopg (one span per SQL statement)
and aio-pika (publish/consume spans; context travels in message headers, so
order-api -> RabbitMQ -> worker is ONE trace).
"""
from __future__ import annotations

import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

log = logging.getLogger(__name__)

_EXCLUDED_URLS = "health,metrics"


def setup_tracing(service: str, *, fastapi_app=None, httpx_client: bool = False,
                  psycopg: bool = False, aio_pika: bool = False) -> trace.Tracer:
    """Configure the global tracer provider and enable instrumentations."""
    if os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        return trace.get_tracer(service)

    resource = Resource.create({
        "service.name": service,
        "service.namespace": "shop",
        "service.version": os.getenv("SERVICE_VERSION", "1.0.0"),
        "deployment.environment": os.getenv("DEPLOYMENT_ENV", "coursework"),
    })
    ratio = float(os.getenv("OTEL_TRACES_SAMPLER_RATIO", "1.0"))
    provider = TracerProvider(resource=resource, sampler=ParentBased(TraceIdRatioBased(ratio)))
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://alloy:4318").rstrip("/")
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=5),
        max_queue_size=4096, schedule_delay_millis=1000))
    trace.set_tracer_provider(provider)

    if fastapi_app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(fastapi_app, excluded_urls=_EXCLUDED_URLS,
                                           exclude_spans=["receive", "send"])
    if httpx_client:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    if psycopg:
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
        PsycopgInstrumentor().instrument(enable_commenter=False)
    if aio_pika:
        from opentelemetry.instrumentation.aio_pika import AioPikaInstrumentor
        AioPikaInstrumentor().instrument()

    log.info("tracing enabled", extra={"otlp_endpoint": endpoint, "sample_ratio": ratio})
    return trace.get_tracer(service)
