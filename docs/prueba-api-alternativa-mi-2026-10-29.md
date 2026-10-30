# Prueba de la API alternativa desde Azure con Managed Identity

Ejecutada el **29/10/2026 a las 17:02, hora de Argentina (UTC-3)**.

La comprobación se ejecutó dentro de `func-pi-epre-dev-hnso27`, mediante la ruta administrativa protegida. Se utilizaron el mismo token de la MI y la misma consulta en ambos endpoints. La configuración del endpoint público EPRE y su consulta de nueve colecciones no se modificaron.

## Identidad y destino

- Principal ID: `076d1b24-1406-43bc-a477-922d03cb5cc0`.
- App ID: `40aeffea-62db-4902-bf97-b5c607b653ad`.
- Tenant: `cddb6514-1d4f-4a4a-a3ed-a1ca3de10829`.
- Credencial ejecutada: únicamente `ManagedIdentityCredential`.
- Audiencia del token: `https://api.fabric.microsoft.com`.
- Workspace en ambos casos: `1873b863-b221-43a0-9522-aed836c91536`.
- Se comprobó que el destino alternativo construido coincide exactamente con el endpoint recibido del usuario.

## Consulta ejecutada

```graphql
query {
  vw_kpi_epres(first: 10) {
    items {
      Metrica
    }
  }
}
```

## Resultados del origen

| API | ID | HTTP | Lectura confirmada |
| --- | --- | --- | --- |
| Original | `7ef769b0-d967-4e06-9b6b-5d97d2c8853f` | 403 | No |
| Alternativa | `6165c71e-2e6b-4806-8dac-dbe8ba35e1c2` | 403 | No |

Ambas respuestas GraphQL fueron vacías y no entregaron un código de error adicional en JSON. La consulta de metadatos del workspace mediante la API REST de Fabric también falló: HTTP 401, código `Unauthorized`.

La ejecución del diagnóstico respondió HTTP 200 con el informe; eso no equivale a una lectura exitosa de Fabric. Las sondas de datos devolvieron 403 y `read_succeeded=false`. Se verificó que el diagnóstico rechace con 401 tanto las solicitudes anónimas como las que utilizan la Function Key ordinaria. No se devolvieron valores de Metrica ni tokens.

## Correlación

- Request ID del diagnóstico: `70733520-a286-4cbc-a0b9-a1c336f73bfa`.
- Trace ID del diagnóstico: `7e10904e423340018e18db1906e62a51`.
- Request ID de Fabric para la API alternativa: `2cd5d2eb-6df1-4e10-8cf3-4aa4ea73c6c1`.
- Request ID de Fabric para la API original: `6c4deb17-bf62-4cac-b885-41ef75911649`.
- Informe completo de metadatos: `dist/fabric-alternate-identity-diagnostic.json`.

## Interpretación y reproducción

Cambiar únicamente el ID de la API no resolvió el rechazo. El resultado no permite determinar que el problema sea SSO, ni se ha inspeccionado el modo de conexión de la API alternativa. Debe revisarse la autorización efectiva de esta MI, incluida la configuración del tenant que habilita el uso de APIs de Fabric para identidades de servicio y el alcance de sus grupos permitidos.

```powershell
.\.venv\Scripts\python.exe -m scripts.check_fabric_identity --target alternate
```

La suite completa incluye 40 pruebas correctas. Referencia para la configuración de tenant: [soporte de identidades en Fabric](https://learn.microsoft.com/en-us/rest/api/fabric/articles/identity-support#service-principal-tenant-setting).
