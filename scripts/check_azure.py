"""Verifica la API desplegada sin imprimir claves ni filas de Fabric."""

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import requests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--subscription', default='d468911c-54a0-4a0b-af8c-938d81aeea30')
    parser.add_argument('--resource-group', default='rg-pi-epre-dev')
    parser.add_argument('--app', default='func-pi-epre-dev-hnso27')
    args = parser.parse_args()
    az = shutil.which('az')
    if not az:
        parser.error('Azure CLI no esta instalado.')

    def azure(*command):
        result = subprocess.run(
            [az, *command, '--subscription', args.subscription,
             '--resource-group', args.resource_group, '--output', 'json'],
            capture_output=True, text=True, check=False, timeout=90,
        )
        if result.returncode:
            # La salida de comandos de configuracion puede contener secretos.
            raise RuntimeError('Azure CLI fallo; comprobar sesion y permisos en el recurso.')
        return json.loads(result.stdout)

    hostname = azure('resource', 'show', '--resource-type', 'Microsoft.Web/sites',
                     '--name', args.app, '--api-version', '2023-12-01',
                     '--query', 'properties.defaultHostName')
    if not hostname or not hostname.endswith('.azurewebsites.net'):
        raise RuntimeError('No se obtuvo el hostname HTTPS de la Function.')
    key = azure('functionapp', 'keys', 'list', '--name', args.app, '--query', 'functionKeys.default')
    if not key:
        raise RuntimeError('No se encontro la Function Key predeterminada.')
    endpoint = f'https://{hostname}/api/datos/epre'
    results = []
    cases = [
        ('sin_clave', {}, None, 401),
        ('clave_invalida', {'x-functions-key': 'invalid-verification-key'}, None, 401),
        ('parametro_invalido', {'x-functions-key': key}, {'unsupported': '1'}, 400),
        ('datos_fabric', {'x-functions-key': key}, None, 200),
    ]
    for name, headers, params, expected in cases:
        trace_id = uuid4().hex
        headers = {**headers, 'traceparent': f'00-{trace_id}-{uuid4().hex[:16]}-01'}
        response = requests.get(endpoint, headers=headers, params=params,
                                timeout=(10, 120), allow_redirects=False)
        record = {
            'case': name, 'status': response.status_code, 'expected': expected,
            'passed': response.status_code == expected,
            'trace_id_sent': trace_id,
            'trace_id_received': response.headers.get('X-Trace-ID'),
            'request_id': response.headers.get('X-Request-ID'),
        }
        if expected != 401:
            record['correlation_passed'] = record['trace_id_received'] == trace_id and bool(record['request_id'])
            record['passed'] = record['passed'] and record['correlation_passed']
        if name == 'datos_fabric' and response.status_code == 200:
            data = response.json()
            record['source'] = data.get('origen')
            record['counts'] = {
                collection: value.get('cantidad_devuelta')
                for collection, value in data.get('colecciones', {}).items()
            }
        results.append(record)
        print(json.dumps(record, ensure_ascii=False))

    # Solo metadatos: nunca guardar headers de solicitud, claves o respuesta de negocio.
    report = {'verified_at': datetime.now(timezone.utc).isoformat(),
              'endpoint': endpoint, 'results': results}
    destination = Path(__file__).resolve().parents[1] / 'dist' / 'azure-verification.json'
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if all(row['passed'] for row in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
