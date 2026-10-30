"""Consulta de lectura real a Fabric. No contiene datos ni proveedores ficticios."""

import logging
from functools import lru_cache
from pathlib import Path

import requests
from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential

from esj_api.config import ServiceUnavailable, Settings
from esj_api.telemetry import log_event, operation, trace_headers, SpanKind

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
COLLECTION_FIELDS = {
    "vw_kpi_epres": ("Metrica", "Fecha", "Hora", "Valor"),
    "demanda_Generacions": ("Fecha_Hora", "Demanda", "Generacion"),
    "carteros": ("fecha_hora", "mensaje"),
    "cantidad_Reclamos": ("Fecha_Hora", "Reclamos_Totales"),
    "reclamos": ("Fecha_Hora", "Descripcion", "Reclamo", "Suministro", "Tipo"),
    "vw_reclamos_gps": ("Tipo", "Latitud", "Longitud", "Suministro", "Reclamo", "Fecha_Hora", "Descripcion"),
    "cantidad_Setas_Afectadas": ("Fecha_Hora", "Cantidad"),
    "setas_Afectadas": ("Fecha_Hora", "ET_Distribuidor", "ID_Seta", "Usuarios_Afectados"),
    "vw_setas_afectadas_gps": ("Tipo", "Latitud", "Longitud", "ET_Distribuidor", "Setas", "Fecha_Hora", "Descripcion"),
}
# None significa que la consulta no fija first; no garantiza todos los registros.
COLLECTION_LIMITS = dict.fromkeys(COLLECTION_FIELDS)
EPRE_QUERY = (Path(__file__).parent / "queries" / "epre.graphql").read_text(encoding="utf-8")

# El SDK puede incluir detalles de autenticación en sus mensajes de error.
# La aplicación registra diagnósticos propios sin respuestas, URLs ni tokens.
logging.getLogger("azure.identity").setLevel(logging.CRITICAL)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.CRITICAL)


class FabricError(Exception):
    """Diagnóstico estático de un fallo de Fabric; nunca su cuerpo de respuesta."""


class FabricTimeout(FabricError):
    pass


@lru_cache(maxsize=1)
def get_credential() -> DefaultAzureCredential:
    # La variable AZURE_TOKEN_CREDENTIALS restringe la cadena a una identidad.
    # El SDK mantiene su caché; se pide un token vigente para cada llamada.
    return DefaultAzureCredential(
        require_envvar=True, process_timeout=15,
        connection_timeout=5, read_timeout=10, retry_total=0,
    )


class FabricProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def fetch(self) -> dict[str, list[dict]]:
        with operation(logger, "fabric.token"):
            token = self._access_token()
        with operation(logger, "fabric.graphql", kind=SpanKind.CLIENT) as span:
            span.set_attribute("peer.service", "fabric-graphql")
            span.set_attribute("http.request.method", "POST")
            span.set_attribute("graphql.operation.name", "epre")
            return self._query(token, span)

    @staticmethod
    def _access_token() -> str:
        try:
            return get_credential().get_token(FABRIC_SCOPE).token
        except (AzureError, ValueError):
            raise ServiceUnavailable(
                "No se pudo obtener el token Fabric. En local verificar az login en el tenant correcto; en Azure verificar Managed Identity."
            ) from None

    def _query(self, token, span) -> dict[str, list[dict]]:
        try:
            with requests.post(
                self.settings.fabric_graphql_endpoint,
                json={"query": EPRE_QUERY},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json", **trace_headers()},
                timeout=(5, 30),
                allow_redirects=False,
            ) as response:
                span.set_attribute("http.response.status_code", response.status_code)
                log_event(logger, "fabric.response", upstream_status=response.status_code)
                if response.status_code in (401, 403):
                    raise ServiceUnavailable(
                        f"Fabric rechazó el acceso (HTTP {response.status_code}). Verificar tenant, permisos Execute de GraphQL y acceso a la fuente de datos."
                    )
                if response.status_code in (429, 503):
                    raise ServiceUnavailable(
                        f"Fabric no está disponible temporalmente (HTTP {response.status_code}). Reintentar más tarde."
                    )
                if response.status_code == 404:
                    raise FabricError("Fabric devolvió HTTP 404. Verificar que el endpoint y el recurso existan y sean visibles para la identidad y tenant utilizados.")
                if response.status_code != 200:
                    raise FabricError(f"Fabric devolvió HTTP {response.status_code}; no se expone su respuesta.")
                try:
                    payload = response.json()
                except ValueError:
                    raise FabricError("Fabric devolvió un cuerpo que no es JSON válido.") from None
        except requests.Timeout:
            raise FabricTimeout("Se agotó el tiempo de conexión o lectura de Fabric.") from None
        except requests.RequestException:
            raise FabricError("No se pudo completar la conexión HTTPS con Fabric.") from None

        return parse_items(payload)


def parse_items(payload: object) -> dict[str, list[dict]]:
    if not isinstance(payload, dict):
        raise FabricError("Respuesta GraphQL inválida: se esperaba un objeto.")
    if payload.get("errors"):
        # GraphQL puede devolver HTTP 200 junto con errors y datos parciales.
        raise FabricError("Fabric devolvió errores GraphQL; se descartan datos parciales. Verificar consulta, esquema y permisos de la fuente.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise FabricError("Respuesta GraphQL incompatible: falta el objeto data.")
    result = {}
    for name, fields in COLLECTION_FIELDS.items():
        collection = data.get(name)
        items = collection.get("items") if isinstance(collection, dict) else None
        limit = COLLECTION_LIMITS[name]
        if not isinstance(items, list) or (limit is not None and len(items) > limit):
            raise FabricError(f"Respuesta incompatible: falta items de {name} o supera el límite solicitado.")
        for item in items:
            if not isinstance(item, dict) or any(field not in item for field in fields):
                raise FabricError(f"Respuesta incompatible: faltan campos solicitados en {name}.")
            if any(item[field] is not None and not isinstance(item[field], (str, int, float, bool)) for field in fields):
                raise FabricError(f"Respuesta incompatible: se esperaban valores escalares o null en {name}.")
        result[name] = items
    return result
