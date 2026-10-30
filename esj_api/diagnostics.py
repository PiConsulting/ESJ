"""Sondas fijas de lectura para diagnóstico administrativo; nunca devuelven filas."""

import base64
import json
import logging
import re
from urllib.parse import urlsplit
from uuid import UUID

import requests

from esj_api.fabric import FABRIC_SCOPE, get_credential
from esj_api.telemetry import SpanKind, log_event, operation, trace_headers

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
VERSION = "2026-10-29.3"
ALTERNATE_API_ID = "6165c71e-2e6b-4806-8dac-dbe8ba35e1c2"
COMPARISON_QUERY = "query { vw_kpi_epres(first: 10) { items { Metrica } } }"
PROBES = (
    ("root", "query { __typename }"),
    ("kpi", "query { vw_kpi_epres(first: 1) { items { Metrica } } }"),
)


class _ProbeFailed(Exception):
    """Marca un fallo sin mensajes del proveedor."""


def _uuid(value):
    try:
        return str(UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def _code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value) else None


def _token_metadata(token):
    # Decodifica sólo metadatos de un token obtenido del SDK; no autoriza usuarios
    # ni sustituye la validación criptográfica del token que realiza Fabric.
    try:
        encoded = token.split(".")[1]
        if len(encoded) > 16000:
            return {"claims_available": False}
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if not isinstance(claims, dict):
            return {"claims_available": False}
    except (ValueError, IndexError, TypeError):
        return {"claims_available": False}
    audience = claims.get("aud")
    if not isinstance(audience, str) or not re.fullmatch(r"[A-Za-z0-9:/._-]{1,200}", audience):
        audience = None
    return {"claims_available": True, "principal_id": _uuid(claims.get("oid")),
            "app_id": _uuid(claims.get("appid") or claims.get("azp")),
            "tenant_id": _uuid(claims.get("tid")), "audience": audience}


def _error_metadata(payload):
    if not isinstance(payload, dict):
        return [], None
    codes = []
    error = payload.get("error")
    if isinstance(error, dict):
        codes.append(_code(error.get("code")))
    codes.append(_code(payload.get("errorCode")))
    errors = payload.get("errors")
    if isinstance(errors, list):
        for item in errors[:10]:
            ext = item.get("extensions") if isinstance(item, dict) else None
            if isinstance(ext, dict):
                codes.append(_code(ext.get("code")))
    return sorted(set(filter(None, codes))), _uuid(payload.get("requestId"))


def _response_metadata(response, payload):
    codes, request_id = _error_metadata(payload)
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if content_type not in {"application/json", "application/problem+json", "text/html", "text/plain"}:
        content_type = "other"
    header_id = next((_uuid(response.headers.get(name)) for name in (
        "x-ms-request-id", "request-id", "requestId", "x-ms-activity-id", "activityId"
    ) if _uuid(response.headers.get(name))), None)
    return {"http_status": response.status_code, "error_codes": codes,
            "upstream_request_id": request_id or header_id,
            "response_format": content_type, "body_bytes": len(response.content)}


def _workspace_probe(token, workspace_id):
    workspace_id = _uuid(workspace_id)
    result = {"probe": "workspace_visibility", "http_status": None, "read_succeeded": False}
    if not workspace_id:
        return {**result, "skipped": "invalid_workspace_id"}
    try:
        with operation(logger, "diagnostic.workspace", kind=SpanKind.CLIENT) as span:
            span.set_attribute("peer.service", "fabric-rest")
            span.set_attribute("http.request.method", "GET")
            try:
                with requests.get(
                    "https://api.fabric.microsoft.com/v1/workspaces/" + workspace_id,
                    headers={"Authorization": "Bearer " + token, "Accept": "application/json", **trace_headers()},
                    timeout=(5, 20), allow_redirects=False,
                ) as response:
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = None
                    result.update(_response_metadata(response, payload))
                    span.set_attribute("http.response.status_code", response.status_code)
                    result["read_succeeded"] = (response.status_code == 200 and isinstance(payload, dict)
                                                and _uuid(payload.get("id")) == workspace_id)
            except requests.Timeout:
                result["transport_error"] = "timeout"
            except requests.RequestException:
                result["transport_error"] = "connection_error"
            log_event(logger, "diagnostic.response", probe="workspace_visibility",
                      upstream_status=result["http_status"], read_succeeded=result["read_succeeded"])
            if not result["read_succeeded"]:
                raise _ProbeFailed()
    except _ProbeFailed:
        pass
    return result


def _probe(endpoint, token, name, query, item_limit=1):
    result = {"probe": name, "http_status": None, "read_succeeded": False}
    try:
        with operation(logger, "diagnostic." + name, kind=SpanKind.CLIENT) as span:
            span.set_attribute("peer.service", "fabric-graphql")
            span.set_attribute("http.request.method", "POST")
            try:
                with requests.post(
                    endpoint, json={"query": query},
                    headers={"Authorization": "Bearer " + token,
                             "Accept": "application/json", **trace_headers()},
                    timeout=(5, 10), allow_redirects=False,
                ) as response:
                    result["http_status"] = response.status_code
                    span.set_attribute("http.response.status_code", response.status_code)
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = None
                    result.update(_response_metadata(response, payload))
                    result["graphql_errors_present"] = bool(payload.get("errors")) if isinstance(payload, dict) else False
                    data = payload.get("data") if isinstance(payload, dict) else None
                    if response.status_code == 200 and not result["graphql_errors_present"] and isinstance(data, dict):
                        if name == "root":
                            result["read_succeeded"] = isinstance(data.get("__typename"), str)
                        else:
                            collection = data.get("vw_kpi_epres")
                            items = collection.get("items") if isinstance(collection, dict) else None
                            if isinstance(items, list) and len(items) <= item_limit and all(
                                isinstance(item, dict) and "Metrica" in item for item in items
                            ):
                                result["read_succeeded"] = True
                                result["item_count"] = len(items)
            except requests.Timeout:
                result["transport_error"] = "timeout"
            except requests.RequestException:
                result["transport_error"] = "connection_error"
            log_event(logger, "diagnostic.response", probe=name,
                      upstream_status=result["http_status"], read_succeeded=result["read_succeeded"])
            if not result["read_succeeded"]:
                raise _ProbeFailed()
    except _ProbeFailed:
        pass
    return result


def diagnose(settings):
    credential = get_credential()
    with operation(logger, "diagnostic.token"):
        token = credential.get_token(FABRIC_SCOPE).token
    match = re.fullmatch(r"/v1/workspaces/([0-9a-f-]+)/graphqlapis/([0-9a-f-]+)/graphql",
                         urlsplit(settings.fabric_graphql_endpoint).path)
    workspace_probe = _workspace_probe(token, match[1] if match else None)
    results = [_probe(settings.fabric_graphql_endpoint, token, name, query) for name, query in PROBES]
    comparisons = []
    if match and _uuid(match[1]) and _uuid(match[2]):
        # Mismo host/workspace, misma consulta y exactamente el mismo token.
        # Sólo cambia el ID de la API; no se admiten destinos suministrados por el cliente.
        alternate = urlsplit(settings.fabric_graphql_endpoint)._replace(
            path=f"/v1/workspaces/{match[1]}/graphqlapis/{ALTERNATE_API_ID}/graphql"
        ).geturl()
        for name, api_id, endpoint in (
            ("original_kpi_first10", match[2], settings.fabric_graphql_endpoint),
            ("alternate_kpi_first10", ALTERNATE_API_ID, alternate),
        ):
            result = _probe(endpoint, token, name, COMPARISON_QUERY, item_limit=10)
            comparisons.append({"api_id": api_id, **result})
    return {"diagnostic_version": VERSION, "scope": FABRIC_SCOPE,
            "credential_chain": [type(item).__name__ for item in getattr(credential, "credentials", ())],
            "token_metadata": _token_metadata(token),
            "workspace_id": _uuid(match[1]) if match else None,
            "api_id": _uuid(match[2]) if match else None,
            "workspace_probe": workspace_probe,
            "comparison_probes": comparisons,
            "probes": results, "data_read_succeeded": results[-1]["read_succeeded"]}
