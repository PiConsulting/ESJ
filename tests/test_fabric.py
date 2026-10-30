"""Red simulada sólo en tests. La aplicación siempre usa el proveedor real."""
import json
import logging
import os
import re
import unittest
from unittest.mock import MagicMock, patch

import azure.functions as func
import requests
from azure.core.exceptions import ClientAuthenticationError

from esj_api.config import ServiceUnavailable, Settings
from esj_api.fabric import COLLECTION_FIELDS, FABRIC_SCOPE, EPRE_QUERY, get_credential
from function_app import app, datos_epre

ENDPOINT = "https://example.z18.graphql.fabric.microsoft.com/v1/workspaces/test/graphqlapis/test/graphql"


def graphql(items):
    return {"data": {
        "vw_kpi_epres": {"items": []},
        "demanda_Generacions": {"items": []},
        "carteros": {"items": []},
        "cantidad_Reclamos": {"items": []},
        "reclamos": {"items": []},
        "vw_reclamos_gps": {"items": []},
        "cantidad_Setas_Afectadas": {"items": []},
        "setas_Afectadas": {"items": items},
        "vw_setas_afectadas_gps": {"items": []},
    }}


class ConfigurationTests(unittest.TestCase):
    def test_endpoint_must_be_https_fabric_without_credentials(self):
        for endpoint in ("", "http://example.graphql.fabric.microsoft.com/graphql", "https://example.com/graphql", "https://example.graphql.fabric.microsoft.com.evil.invalid/graphql", "https://user:secret@example.graphql.fabric.microsoft.com/graphql", ENDPOINT + "?token=secret", ENDPOINT + "#secret"):
            with self.subTest(endpoint=endpoint), patch.dict(os.environ, {"FABRIC_GRAPHQL_ENDPOINT": endpoint, "AZURE_TOKEN_CREDENTIALS": "AzureCliCredential"}, clear=True):
                with self.assertRaises(ServiceUnavailable):
                    Settings.from_env()

    def test_only_explicit_cli_or_managed_identity_is_accepted(self):
        for credential in ("", "dev", "EnvironmentCredential", "InteractiveBrowserCredential", "AzureCliCredential", "ManagedIdentityCredential"):
            with self.subTest(credential=credential), patch.dict(os.environ, {"FABRIC_GRAPHQL_ENDPOINT": ENDPOINT, "AZURE_TOKEN_CREDENTIALS": credential}, clear=True):
                if credential in ("AzureCliCredential", "ManagedIdentityCredential"):
                    self.assertEqual(Settings.from_env().fabric_graphql_endpoint, ENDPOINT)
                else:
                    with self.assertRaises(ServiceUnavailable):
                        Settings.from_env()

    def test_credential_requires_explicit_selection_and_is_reused(self):
        get_credential.cache_clear()
        self.addCleanup(get_credential.cache_clear)
        with patch("esj_api.fabric.DefaultAzureCredential") as constructor:
            self.assertIs(get_credential(), get_credential())
            constructor.assert_called_once()
            self.assertTrue(constructor.call_args.kwargs["require_envvar"])


class HttpTests(unittest.TestCase):
    def setUp(self):
        # Evita ruido por los fallos esperados; assertLogs sigue capturándolos.
        quiet = logging.NullHandler()
        logging.getLogger().addHandler(quiet)
        self.addCleanup(logging.getLogger().removeHandler, quiet)
        environment = patch.dict(os.environ, {"FABRIC_GRAPHQL_ENDPOINT": ENDPOINT, "AZURE_TOKEN_CREDENTIALS": "AzureCliCredential"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        credential_patch = patch("esj_api.fabric.get_credential")
        self.credential = credential_patch.start().return_value
        self.credential.get_token.return_value.token = "PRIVATE_TOKEN"
        self.addCleanup(credential_patch.stop)
        post_patch = patch("esj_api.fabric.requests.post")
        self.post = post_patch.start()
        self.addCleanup(post_patch.stop)
        self.upstream = MagicMock()
        self.upstream.status_code = 200
        self.upstream.json.return_value = graphql([])
        self.post.return_value.__enter__.return_value = self.upstream

    def request(self, params=None, body=b""):
        response = datos_epre(func.HttpRequest(method="GET", url="http://localhost/api/datos/epre", params=params or {}, body=body))
        self.assertEqual(response.mimetype, "application/json")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        return response, json.loads(response.get_body())

    def assert_failure(self, expected):
        with self.assertLogs("function_app", level="ERROR") as logs:
            response, payload = self.request()
        self.assertEqual(response.status_code, expected)
        self.assertNotIn("colecciones", payload)
        self.assertEqual(payload["error"]["request_id"], response.headers["X-Request-ID"])
        self.assertIn(payload["error"]["request_id"], str(logs.output))
        for secret in ("PRIVATE_TOKEN", "PRIVATE_DETAIL", ENDPOINT):
            self.assertNotIn(secret, str(payload) + str(logs.output))

    def test_query_token_and_public_mapping(self):
        rows = {
            "vw_kpi_epres": {"Metrica": "TEST_KPI", "Fecha": "2026-10-26", "Hora": "08:30:00", "Valor": "12.3400"},
            "demanda_Generacions": {"Fecha_Hora": "2026-10-26T08:30:00", "Demanda": 12.5, "Generacion": None},
            "carteros": {"fecha_hora": "2026-10-26T08:30:00", "mensaje": "Mensaje de prueba"},
            "cantidad_Reclamos": {"Fecha_Hora": None, "Reclamos_Totales": 0},
            "reclamos": {"Fecha_Hora": None, "Descripcion": "Prueba", "Reclamo": "0001", "Suministro": "0002", "Tipo": "TEST"},
            "vw_reclamos_gps": {"Tipo": "TEST", "Latitud": -31.5, "Longitud": -68.5, "Suministro": "0002", "Reclamo": "0001", "Fecha_Hora": None, "Descripcion": "Prueba"},
            "cantidad_Setas_Afectadas": {"Fecha_Hora": None, "Cantidad": 3},
            "setas_Afectadas": {"Fecha_Hora": None, "ET_Distribuidor": "TEST", "ID_Seta": "001", "Usuarios_Afectados": 25},
            "vw_setas_afectadas_gps": {"Tipo": "TEST", "Latitud": None, "Longitud": None, "ET_Distribuidor": "TEST", "Setas": 2, "Fecha_Hora": None, "Descripcion": "Prueba"},
        }
        self.upstream.json.return_value = {"data": {name: {"items": [dict(row, internal="PRIVATE_DETAIL")]} for name, row in rows.items()}}
        response, payload = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload, {"origen": "fabric", "colecciones": {name: {"cantidad_devuelta": 1, "limite_solicitado": None, "items": [row]} for name, row in rows.items()}})
        self.credential.get_token.assert_called_once_with(FABRIC_SCOPE)
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.args, (ENDPOINT,))
        self.assertEqual(self.post.call_args.kwargs["json"], {"query": EPRE_QUERY})
        headers = self.post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer PRIVATE_TOKEN")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["traceparent"].split("-")[1], response.headers["X-Trace-ID"])
        self.assertEqual(self.post.call_args.kwargs["timeout"], (5, 30))
        self.assertFalse(self.post.call_args.kwargs["allow_redirects"])
        selections = re.findall(r"(\w+)\s*\{\s*items\s*\{([^{}]+)\}\s*\}", EPRE_QUERY)
        self.assertEqual({name: fields.split() for name, fields in selections}, {name: list(row) for name, row in rows.items()})
        self.assertNotIn("first:", EPRE_QUERY)

    def test_empty_results_are_valid(self):
        response, payload = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["colecciones"]), 9)
        for collection in payload["colecciones"].values():
            self.assertEqual(collection["cantidad_devuelta"], 0)
            self.assertEqual(collection["items"], [])

    def test_kpis_are_not_truncated_to_ten(self):
        rows = [{"Metrica": "TEST", "Fecha": "2026-10-23", "Hora": None, "Valor": n} for n in range(15)]
        self.upstream.json.return_value["data"]["vw_kpi_epres"]["items"] = rows
        response, payload = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["colecciones"]["vw_kpi_epres"]["items"], rows)
        self.assertIsNone(payload["colecciones"]["vw_kpi_epres"]["limite_solicitado"])

    def test_each_collection_is_required(self):
        for name in COLLECTION_FIELDS:
            with self.subTest(collection=name):
                payload = graphql([])
                del payload["data"][name]
                self.upstream.json.return_value = payload
                self.assert_failure(502)

    def test_each_requested_field_is_required_but_can_be_null(self):
        for name, fields in COLLECTION_FIELDS.items():
            for missing in fields:
                with self.subTest(collection=name, field=missing):
                    payload = graphql([])
                    payload["data"][name]["items"] = [{field: None for field in fields if field != missing}]
                    self.upstream.json.return_value = payload
                    self.assert_failure(502)

    def test_setas_no_longer_have_a_limit_of_ten(self):
        rows = [{"Fecha_Hora": None, "ET_Distribuidor": "TEST", "ID_Seta": n, "Usuarios_Afectados": 0} for n in range(15)]
        self.upstream.json.return_value["data"]["setas_Afectadas"]["items"] = rows
        response, payload = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["colecciones"]["setas_Afectadas"]["items"], rows)
        self.assertIsNone(payload["colecciones"]["setas_Afectadas"]["limite_solicitado"])

    def test_obsolete_filters_and_body_are_rejected_before_network(self):
        for params, body in (({"desde": "2026-01-01"}, b""), ({"hasta": "2026-01-31"}, b""), ({"region": "norte"}, b""), ({"first": "100"}, b""), ({}, b"{}")):
            with self.subTest(params=params, body=body):
                response, payload = self.request(params, body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(payload["error"]["request_id"], response.headers["X-Request-ID"])
        self.post.assert_not_called()
        self.credential.get_token.assert_not_called()

    def test_host_key_is_not_forwarded_to_fabric(self):
        response, _ = self.request({"code": "PRIVATE_FUNCTION_KEY"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("PRIVATE_FUNCTION_KEY", str(self.post.call_args))

    def test_missing_configuration_has_no_fallback(self):
        os.environ.pop("FABRIC_GRAPHQL_ENDPOINT")
        self.assert_failure(503)
        self.post.assert_not_called()

    def test_authentication_errors_are_safe(self):
        self.credential.get_token.side_effect = ClientAuthenticationError("PRIVATE_DETAIL")
        self.assert_failure(503)
        self.post.assert_not_called()

    def test_upstream_statuses_are_safe(self):
        for upstream, expected in ((401, 503), (403, 503), (404, 502), (429, 503), (503, 503), (500, 502), (302, 502)):
            with self.subTest(upstream=upstream):
                self.upstream.status_code = upstream
                self.upstream.json.return_value = {"secret": "PRIVATE_DETAIL"}
                self.assert_failure(expected)

    def test_graphql_errors_discard_partial_data(self):
        payload = graphql([{"ET_Distribuidor": "PARTIAL"}])
        payload["errors"] = [{"message": "PRIVATE_DETAIL"}]
        self.upstream.json.return_value = payload
        self.assert_failure(502)

    def test_invalid_shape_never_becomes_empty_success(self):
        for malformed in (None, [], {}, {"data": None}, {"data": {"setas_Afectadas": None}}, graphql(None), graphql([{}]), graphql([{"ET_Distribuidor": {"nested": 1}}]), graphql([{"ET_Distribuidor": None}] * 11)):
            with self.subTest(payload=malformed):
                self.upstream.json.return_value = malformed
                self.assert_failure(502)

    def test_invalid_json_is_safe(self):
        self.upstream.json.side_effect = ValueError("PRIVATE_DETAIL")
        self.assert_failure(502)

    def test_network_timeouts_and_unexpected_errors_are_safe(self):
        for error, status in ((requests.Timeout("PRIVATE_DETAIL"), 504), (requests.ConnectionError("PRIVATE_DETAIL"), 502), (RuntimeError("PRIVATE_DETAIL"), 500)):
            with self.subTest(error=type(error).__name__):
                self.post.side_effect = error
                self.assert_failure(status)

    def test_trigger_has_new_route_get_and_function_auth(self):
        bindings = app.get_functions()[0].get_bindings_dict()["bindings"]
        trigger = next(binding for binding in bindings if binding["type"] == "httpTrigger")
        self.assertEqual(trigger["authLevel"], func.AuthLevel.FUNCTION)
        self.assertEqual(trigger["methods"], [func.HttpMethod.GET])
        self.assertEqual(trigger["route"], "datos/epre")


if __name__ == "__main__":
    unittest.main()
