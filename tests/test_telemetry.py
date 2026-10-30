import json
import logging
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import azure.functions as func
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from esj_api import telemetry
from esj_api.config import ServiceUnavailable, Settings
from esj_api.fabric import COLLECTION_FIELDS
from function_app import datos_epre

TRACE_ID = "0123456789abcdef0123456789abcdef"
PARENT_ID = "0123456789abcdef"


def host_context(trace_id=TRACE_ID):
    return SimpleNamespace(invocation_id="invocation-test", trace_context=SimpleNamespace(
        Traceparent=f"00-{trace_id}-{PARENT_ID}-01", Tracestate="",
    ))


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.addCleanup(self.provider.shutdown)
        self.tracer_patch = patch.object(telemetry, "tracer", self.provider.get_tracer("test"))
        self.tracer_patch.start()
        self.addCleanup(self.tracer_patch.stop)
        env = patch.dict(os.environ, {
            "FABRIC_GRAPHQL_ENDPOINT": "https://example.graphql.fabric.microsoft.com/graphql",
            "AZURE_TOKEN_CREDENTIALS": "AzureCliCredential",
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)
        credential = patch("esj_api.fabric.get_credential")
        self.credential = credential.start().return_value
        self.credential.get_token.return_value.token = "TOKEN_SECRET"
        self.addCleanup(credential.stop)
        post = patch("esj_api.fabric.requests.post")
        self.post = post.start()
        self.addCleanup(post.stop)
        self.response = MagicMock()
        self.response.status_code = 200
        self.response.json.return_value = {"data": {name: {"items": []} for name in COLLECTION_FIELDS}}
        self.post.return_value.__enter__.return_value = self.response

    def request(self, **kwargs):
        return func.HttpRequest(method="GET", url="http://localhost/api/datos/epre", body=b"", **kwargs)

    def test_host_parent_logs_dependencies_and_response_share_trace(self):
        # El contexto del host prevalece sobre headers proporcionados por el cliente.
        request = self.request(headers={"traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"})
        with self.assertLogs(level="INFO") as logs:
            response = datos_epre(request, host_context())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Trace-ID"], TRACE_ID)
        spans = {span.name: span for span in self.exporter.get_finished_spans()}
        self.assertEqual(set(spans), {"esj.request", "fabric.token", "fabric.graphql"})
        self.assertEqual(spans["esj.request"].parent.span_id, int(PARENT_ID, 16))
        self.assertEqual(spans["fabric.graphql"].kind, SpanKind.CLIENT)
        for name in ("fabric.token", "fabric.graphql"):
            self.assertEqual(spans[name].parent.span_id, spans["esj.request"].context.span_id)
        self.assertEqual(spans["fabric.graphql"].attributes["http.response.status_code"], 200)
        outgoing = self.post.call_args.kwargs["headers"]["traceparent"].split("-")
        self.assertEqual(outgoing[1], TRACE_ID)
        self.assertEqual(int(outgoing[2], 16), spans["fabric.graphql"].context.span_id)
        events = [json.loads(record.getMessage()) for record in logs.records]
        for event in events:
            self.assertEqual(event["trace_id"], TRACE_ID)
            self.assertEqual(event["request_id"], response.headers["X-Request-ID"])
            self.assertEqual(event["invocation_id"], "invocation-test")
        completed = [event for event in events if event["event"] == "request.completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["status_code"], 200)
        self.assertGreaterEqual(completed[0]["duration_ms"], 0)

    def test_errors_and_spans_do_not_include_sensitive_payloads(self):
        self.response.json.return_value = {"errors": [{"message": "PRIVATE_GRAPHQL_VALUE"}]}
        with self.assertLogs(level="INFO") as logs:
            response = datos_epre(self.request(headers={"x-functions-key": "FUNCTION_KEY_SECRET"}), host_context())
        self.assertEqual(response.status_code, 502)
        spans = {span.name: span for span in self.exporter.get_finished_spans()}
        self.assertEqual(spans["fabric.graphql"].status.status_code, StatusCode.ERROR)
        self.assertEqual(spans["esj.request"].status.status_code, StatusCode.ERROR)
        serialized = str(logs.output) + "".join(span.to_json() for span in spans.values())
        for secret in ("PRIVATE_GRAPHQL_VALUE", "FUNCTION_KEY_SECRET", "TOKEN_SECRET", "example.graphql.fabric.microsoft.com"):
            self.assertNotIn(secret, serialized)
        self.assertTrue(all(not span.events for span in spans.values()))

    def test_success_logs_counts_without_values(self):
        self.response.json.return_value["data"]["carteros"]["items"] = [{"fecha_hora": None, "mensaje": "PRIVATE_RECORD_VALUE"}]
        with self.assertLogs(level="INFO") as logs:
            response = datos_epre(self.request(), host_context())
        self.assertEqual(response.status_code, 200)
        events = [json.loads(record.getMessage()) for record in logs.records]
        counts = next(event["counts"] for event in events if event["event"] == "fabric.results")
        self.assertEqual(counts["carteros"], 1)
        self.assertNotIn("PRIVATE_RECORD_VALUE", str(logs.output))

    def test_invalid_trace_header_generates_a_valid_new_trace(self):
        with self.assertLogs(level="INFO"):
            response = datos_epre(self.request(headers={"traceparent": "invalid"}))
        self.assertRegex(response.headers["X-Trace-ID"], r"^[0-9a-f]{32}$")
        self.assertNotEqual(response.headers["X-Trace-ID"], "0" * 32)

    def test_context_is_isolated_between_threads_and_reset_after_errors(self):
        def run(number):
            trace_id = f"{number:032x}"
            request_id = None
            try:
                with telemetry.request_scope(self.request(), host_context(trace_id)) as (request_id, actual, _):
                    self.assertEqual(actual, trace_id)
                    with telemetry.operation(logging.getLogger("esj_test"), "test.operation"):
                        raise RuntimeError("PRIVATE_EXCEPTION")
            except RuntimeError:
                pass
            self.assertIsNone(telemetry._request.get())
            return request_id

        with self.assertLogs(level="INFO") as logs:
            with ThreadPoolExecutor(max_workers=2) as pool:
                ids = list(pool.map(run, [1, 2]))
        self.assertEqual(len(set(ids)), 2)
        events = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual({(event["request_id"], event["trace_id"]) for event in events}, {(ids[0], f"{1:032x}"), (ids[1], f"{2:032x}")})
        self.assertNotIn("PRIVATE_EXCEPTION", str(logs.output))

    def test_bad_input_is_logged_without_contacting_fabric(self):
        with self.assertLogs(level="INFO") as logs:
            response = datos_epre(self.request(params={"region": "PRIVATE_FILTER"}), host_context())
        self.assertEqual(response.status_code, 400)
        self.post.assert_not_called()
        events = [json.loads(record.getMessage()) for record in logs.records]
        self.assertEqual([event["event"] for event in events], ["request.started", "request.rejected", "request.completed"])
        self.assertEqual(events[-1]["status_code"], 400)
        self.assertNotIn("PRIVATE_FILTER", str(logs.output))


class AzureConfigurationTests(unittest.TestCase):
    def test_core_tools_local_hostname_allows_cli(self):
        with patch.dict(os.environ, {
            "FABRIC_GRAPHQL_ENDPOINT": "https://example.graphql.fabric.microsoft.com/graphql",
            "AZURE_TOKEN_CREDENTIALS": "AzureCliCredential", "WEBSITE_HOSTNAME": "localhost:7071",
        }, clear=True):
            Settings.from_env()

    def test_cloud_rejects_personal_cli_credentials(self):
        with patch.dict(os.environ, {
            "FABRIC_GRAPHQL_ENDPOINT": "https://example.graphql.fabric.microsoft.com/graphql",
            "AZURE_TOKEN_CREDENTIALS": "AzureCliCredential", "WEBSITE_INSTANCE_ID": "azure-instance-test",
        }, clear=True):
            with self.assertRaises(ServiceUnavailable):
                Settings.from_env()
            os.environ["AZURE_TOKEN_CREDENTIALS"] = "ManagedIdentityCredential"
            Settings.from_env()

    def test_no_exporter_or_background_export_without_connection_string(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(telemetry, "AzureMonitorTraceExporter") as exporter:
            provider = telemetry.build_trace_provider()
            exporter.assert_not_called()
            provider.shutdown()

    def test_exporter_only_for_traces_when_configured(self):
        with patch.dict(os.environ, {"APPLICATIONINSIGHTS_CONNECTION_STRING": "TEST_CONNECTION"}, clear=True):
            with patch.object(telemetry, "AzureMonitorTraceExporter") as exporter:
                provider = telemetry.build_trace_provider()
                exporter.assert_called_once_with(connection_string="TEST_CONNECTION", disable_offline_storage=True)
                provider.shutdown()


if __name__ == "__main__":
    unittest.main()
