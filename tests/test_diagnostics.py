import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import azure.functions as func

from esj_api import diagnostics
from esj_api.config import Settings
from function_app import data_access_diagnostic


OID = "11111111-1111-1111-1111-111111111111"
APP_ID = "22222222-2222-2222-2222-222222222222"
TENANT = "33333333-3333-3333-3333-333333333333"


class DiagnosticTests(unittest.TestCase):
    def token(self):
        claims = {"oid": OID, "appid": APP_ID, "tid": TENANT,
                  "aud": "https://api.fabric.microsoft.com", "private_claim": "PRIVATE_CLAIM"}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        return "HEADER." + payload + ".TOKEN_SECRET"

    def response(self, status, payload):
        result = MagicMock()
        result.status_code = status
        result.json.return_value = payload
        result.headers = {}
        result.__enter__.return_value = result
        return result

    def test_minimal_read_uses_fixed_queries_and_never_returns_rows_or_token(self):
        token = self.token()
        credential = MagicMock()
        credential.get_token.return_value.token = token
        responses = [self.response(200, {"data": {"__typename": "Query"}}),
                     self.response(200, {"data": {"vw_kpi_epres": {"items": [{"Metrica": "PRIVATE_VALUE"}]}}})]
        with patch.object(diagnostics, "get_credential", return_value=credential), \
             patch.object(diagnostics.requests, "post", side_effect=responses) as post:
            report = diagnostics.diagnose(Settings("https://example.graphql.fabric.microsoft.com/graphql"))
        self.assertTrue(report["data_read_succeeded"])
        self.assertEqual(report["probes"][1]["item_count"], 1)
        self.assertEqual(report["token_metadata"]["principal_id"], OID)
        credential.get_token.assert_called_once_with(diagnostics.FABRIC_SCOPE)
        self.assertEqual([call.kwargs["json"]["query"] for call in post.call_args_list],
                         [query for _, query in diagnostics.PROBES])
        for call in post.call_args_list:
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer " + token)
            self.assertFalse(call.kwargs["allow_redirects"])
        serialized = json.dumps(report)
        for secret in [token, "TOKEN_SECRET", "PRIVATE_VALUE", "PRIVATE_CLAIM"]:
            self.assertNotIn(secret, serialized)

    def test_forbidden_reports_only_safe_error_codes_and_marks_read_failure(self):
        response = self.response(403, {"errorCode": "InsufficientPrivileges", "requestId": OID,
                                       "message": "TOKEN_SECRET PRIVATE_VALUE"})
        credential = MagicMock()
        credential.get_token.return_value.token = self.token()
        with patch.object(diagnostics, "get_credential", return_value=credential), \
             patch.object(diagnostics.requests, "post", return_value=response), \
             self.assertLogs("esj_api.diagnostics", "INFO") as logs:
            report = diagnostics.diagnose(Settings("https://example.graphql.fabric.microsoft.com/graphql"))
        self.assertFalse(report["data_read_succeeded"])
        self.assertEqual(report["probes"][0]["error_codes"], ["InsufficientPrivileges"])
        self.assertEqual(report["probes"][0]["upstream_request_id"], OID)
        self.assertNotIn("TOKEN_SECRET", json.dumps(report) + str(logs.output))
        self.assertNotIn("PRIVATE_VALUE", json.dumps(report) + str(logs.output))

    def test_graphql_errors_even_with_http_200_do_not_prove_read_access(self):
        response = self.response(200, {"data": {"vw_kpi_epres": {"items": []}},
                                       "errors": [{"message": "PRIVATE_VALUE",
                                                   "extensions": {"code": "FORBIDDEN"}}]})
        with patch.object(diagnostics.requests, "post", return_value=response):
            report = diagnostics._probe("https://example.invalid", "TOKEN_SECRET", "kpi", "fixed")
        self.assertFalse(report["read_succeeded"])
        self.assertEqual(report["error_codes"], ["FORBIDDEN"])

    def test_invalid_token_claims_and_untrusted_error_codes_are_omitted(self):
        self.assertEqual(diagnostics._token_metadata("not-a-jwt"), {"claims_available": False})
        codes, request_id = diagnostics._error_metadata({"errorCode": "Bearer TOKEN_SECRET", "requestId": "bad"})
        self.assertEqual(codes, [])
        self.assertIsNone(request_id)

    def test_diagnostic_does_not_accept_queries_from_the_client(self):
        request = func.HttpRequest(method="POST", url="https://example.invalid", body=b'{"query":"CUSTOM_QUERY"}')
        with patch("function_app.diagnose") as probe:
            response = data_access_diagnostic(request)
        self.assertEqual(response.status_code, 400)
        probe.assert_not_called()

    def test_diagnostic_route_requires_admin_auth(self):
        function = data_access_diagnostic.build(func.AuthLevel.FUNCTION)
        binding = next(b for b in function.get_bindings_dict()["bindings"] if b["type"] == "httpTrigger")
        self.assertEqual(binding["authLevel"], func.AuthLevel.ADMIN)
        self.assertEqual(binding["methods"], [func.HttpMethod.POST])

    def test_workspace_probe_confirms_visibility_without_returning_workspace_data(self):
        response = self.response(200, {"id": OID, "displayName": "PRIVATE_WORKSPACE", "description": "PRIVATE_DETAILS"})
        with patch.object(diagnostics.requests, "get", return_value=response) as get:
            report = diagnostics._workspace_probe("TOKEN_SECRET", OID)
        self.assertTrue(report["read_succeeded"])
        self.assertEqual(get.call_args.args[0], "https://api.fabric.microsoft.com/v1/workspaces/" + OID)
        self.assertNotIn("PRIVATE_WORKSPACE", json.dumps(report))
        self.assertNotIn("PRIVATE_DETAILS", json.dumps(report))
        self.assertNotIn("TOKEN_SECRET", json.dumps(report))

    def test_workspace_probe_sanitizes_denial_and_never_follows_redirects(self):
        response = self.response(403, {"errorCode": "InsufficientPrivileges", "message": "PRIVATE_DETAILS"})
        response.headers = {"Content-Type": "application/json", "requestId": APP_ID}
        with patch.object(diagnostics.requests, "get", return_value=response) as get, \
             self.assertLogs("esj_api.diagnostics", "INFO"):
            report = diagnostics._workspace_probe("TOKEN_SECRET", OID)
        self.assertFalse(report["read_succeeded"])
        self.assertEqual(report["error_codes"], ["InsufficientPrivileges"])
        self.assertEqual(report["upstream_request_id"], APP_ID)
        self.assertEqual(report["response_format"], "application/json")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertNotIn("PRIVATE_DETAILS", json.dumps(report))

    def test_endpoint_comparison_uses_same_token_and_exact_first10_query(self):
        credential = MagicMock()
        credential.get_token.return_value.token = self.token()
        endpoint = f"https://example.graphql.fabric.microsoft.com/v1/workspaces/{OID}/graphqlapis/{APP_ID}/graphql"
        denied = self.response(403, {})
        allowed = self.response(200, {"data": {"vw_kpi_epres": {"items": [{"Metrica": "PRIVATE_VALUE"}] * 7}}})
        with patch.object(diagnostics, "get_credential", return_value=credential), \
             patch.object(diagnostics.requests, "get", return_value=denied), \
             patch.object(diagnostics.requests, "post", side_effect=[denied, denied, denied, allowed]) as post, \
             self.assertLogs("esj_api.diagnostics", "INFO"):
            report = diagnostics.diagnose(Settings(endpoint))
        original, alternate = report["comparison_probes"]
        self.assertFalse(original["read_succeeded"])
        self.assertTrue(alternate["read_succeeded"])
        self.assertEqual(alternate["item_count"], 7)
        self.assertEqual(alternate["api_id"], diagnostics.ALTERNATE_API_ID)
        self.assertFalse(report["data_read_succeeded"])
        calls = post.call_args_list[-2:]
        self.assertEqual(calls[0].kwargs["headers"]["Authorization"], calls[1].kwargs["headers"]["Authorization"])
        self.assertEqual(calls[0].kwargs["json"], {"query": diagnostics.COMPARISON_QUERY})
        self.assertEqual(calls[1].kwargs["json"], {"query": diagnostics.COMPARISON_QUERY})
        self.assertEqual(calls[0].args[0], endpoint)
        self.assertEqual(calls[1].args[0], endpoint.replace(APP_ID, diagnostics.ALTERNATE_API_ID))
        self.assertNotIn("PRIVATE_VALUE", json.dumps(report))

    def test_first10_probe_rejects_more_than_ten_items(self):
        response = self.response(200, {"data": {"vw_kpi_epres": {"items": [{"Metrica": "PRIVATE_VALUE"}] * 11}}})
        with patch.object(diagnostics.requests, "post", return_value=response), \
             self.assertLogs("esj_api.diagnostics", "INFO"):
            report = diagnostics._probe("https://example.invalid", "TOKEN_SECRET", "alternate_kpi_first10",
                                        diagnostics.COMPARISON_QUERY, item_limit=10)
        self.assertFalse(report["read_succeeded"])
        self.assertNotIn("PRIVATE_VALUE", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
