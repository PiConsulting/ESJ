"""Entrada HTTP provisional para la API ESJ / EPRE."""

import json
import logging
import traceback
from time import perf_counter

import azure.functions as func

from esj_api.config import EPRE_ROUTE, ServiceUnavailable, Settings
from esj_api.fabric import FabricError, FabricProvider, FabricTimeout
from esj_api.transform import to_public_epre
from esj_api.telemetry import log_event, request_scope, StatusCode
from esj_api.diagnostics import diagnose

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ValidationError(ValueError):
    """Error público de la solicitud HTTP, sin reflejar valores recibidos."""


def json_response(payload: dict, status: int, request_id: str, trace_id: str) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        status_code=status,
        mimetype="application/json",
        charset="utf-8",
        headers={"X-Request-ID": request_id, "X-Trace-ID": trace_id, "Cache-Control": "no-store"},
    )


@app.route(route=EPRE_ROUTE, methods=["GET"])
def datos_epre(req: func.HttpRequest, context: func.Context = None) -> func.HttpResponse:
    with request_scope(req, context) as (request_id, trace_id, span):
        started = perf_counter()
        log_event(logger, "request.started", method="GET", route=EPRE_ROUTE)
        response = handle_request(req, request_id, trace_id)
        span.set_attribute("http.response.status_code", response.status_code)
        span.set_status(StatusCode.ERROR if response.status_code >= 500 else StatusCode.OK)
        log_event(logger, "request.completed",
                  logging.ERROR if response.status_code >= 500 else logging.WARNING if response.status_code >= 400 else logging.INFO,
                  status_code=response.status_code, duration_ms=round((perf_counter() - started) * 1000, 2))
        return response


def handle_request(req: func.HttpRequest, request_id: str, trace_id: str) -> func.HttpResponse:
    try:
        if req.get_body():
            raise ValidationError("El GET debe tener el body vacío; usá la query string.")
        # 'code' es el parámetro de autenticación reservado por Azure Functions.
        # No aceptar filtros que la consulta real todavía no implementa.
        if set(req.params) - {"code"}:
            raise ValidationError("Esta consulta no admite filtros ni parámetros de datos.")
        items = FabricProvider(Settings.from_env()).fetch()
        log_event(logger, "fabric.results", counts={name: len(rows) for name, rows in items.items()})
        return json_response(to_public_epre(items), 200, request_id, trace_id)
    except ValidationError as exc:
        status, code, message = 400, "solicitud_invalida", str(exc)
        log_event(logger, "request.rejected", logging.WARNING, error_code=code)
    except ServiceUnavailable as exc:
        # Mensajes estáticos del código: nunca valores de configuración ni URLs.
        log_event(logger, "request.failed", logging.ERROR, error_code="servicio_no_disponible", reason=str(exc))
        status, code, message = (
            503,
            "servicio_no_disponible",
            "El servicio de datos no está disponible. Contactá al equipo técnico con el request_id.",
        )
    except FabricTimeout as exc:
        log_event(logger, "request.failed", logging.ERROR, error_code="tiempo_agotado", reason=str(exc))
        status, code, message = 504, "tiempo_agotado", "El servicio de datos no respondió a tiempo."
    except FabricError as exc:
        log_event(logger, "request.failed", logging.ERROR, error_code="error_proveedor", reason=str(exc))
        status, code, message = 502, "error_proveedor", "No se pudo obtener una respuesta válida del servicio de datos."
    except Exception as exc:
        # Sólo ubicaciones del stack: los mensajes de excepciones externas pueden
        # contener tokens, URLs o respuestas del proveedor.
        frames = traceback.extract_tb(exc.__traceback__)
        locations = " > ".join(f"{frame.name}:{frame.lineno}" for frame in frames)
        log_event(logger, "request.failed", logging.ERROR, error_code="error_interno", error_type=type(exc).__name__, locations=locations)
        status, code, message = 500, "error_interno", "No se pudo procesar la solicitud."
    return json_response(
        {"error": {"codigo": code, "mensaje": message, "request_id": request_id}},
        status,
        request_id,
        trace_id,
    )


@app.route(route="internal/diagnostics/data-access", methods=["POST"], auth_level=func.AuthLevel.ADMIN)
def data_access_diagnostic(req: func.HttpRequest, context: func.Context = None) -> func.HttpResponse:
    """Sólo clave maestra: dos sondas fijas y metadatos, sin filas ni consultas del cliente."""
    with request_scope(req, context) as (request_id, trace_id, span):
        if req.get_body() or req.params:
            return json_response({"error": {"codigo": "solicitud_invalida", "request_id": request_id}},
                                 400, request_id, trace_id)
        try:
            report = diagnose(Settings.from_env())
        except Exception as exc:
            span.set_status(StatusCode.ERROR)
            log_event(logger, "diagnostic.failed", logging.ERROR, error_type=type(exc).__name__)
            return json_response({"error": {"codigo": "diagnostico_no_disponible", "request_id": request_id}},
                                 503, request_id, trace_id)
        return json_response(report, 200, request_id, trace_id)
