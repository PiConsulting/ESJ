"""Prueba real sin exponer tokens, URL del entorno ni valores de los registros.

Ejecutar desde la raíz: python -m scripts.check_fabric
"""
import json
import os
from pathlib import Path

import azure.functions as func

from function_app import datos_epre


def main() -> int:
    settings_file = Path(__file__).resolve().parents[1] / "local.settings.json"
    if settings_file.exists():
        values = json.loads(settings_file.read_text(encoding="utf-8-sig"))["Values"]
        for name in ("FABRIC_GRAPHQL_ENDPOINT", "AZURE_TOKEN_CREDENTIALS", "AZURE_CLIENT_ID"):
            if name in values:
                os.environ[name] = values[name]
    response = datos_epre(func.HttpRequest(method="GET", url="http://localhost/api/datos/epre", body=b""))
    payload = json.loads(response.get_body())
    print(f"HTTP {response.status_code}")
    if response.status_code != 200:
        print(json.dumps(payload, ensure_ascii=False))
        return 1
    print(f"Origen: {payload['origen']}")
    for name, collection in payload["colecciones"].items():
        limit = collection["limite_solicitado"]
        print(f"{name}: {collection['cantidad_devuelta']} registros; límite solicitado: {limit if limit is not None else 'no especificado'}")
    print("Valores omitidos en este diagnóstico. La consulta no recorre páginas adicionales.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
