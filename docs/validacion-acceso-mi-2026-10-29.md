# Validación de la integración y del acceso con Managed Identity

Fecha de la prueba: **29/10/2026, 16:35 (Argentina, UTC-3)**. Ambiente: desarrollo, `rg-pi-epre-dev`.

## Resultado

No se detectó una discrepancia de endpoint, tenant, identidad, scope o formato de solicitud en las comprobaciones realizadas. La consulta completa funciona con la cuenta de desarrollo y responde HTTP 200. Desde Azure, la identidad administrada recibe HTTP 403 tanto con una consulta mínima de esquema como con la lectura de un único KPI. La consulta pública completa sigue respondiendo 503, originado por ese rechazo 403.

Esto descarta que el rechazo observado dependa exclusivamente de solicitar las nueve colecciones juntas. No identifica por sí solo el permiso efectivo, la configuración de tenant o la política de autorización que produce el rechazo en Fabric.

## Configuración comprobada en Azure y Entra

| Comprobación | Resultado |
| --- | --- |
| Function | `func-pi-epre-dev-hnso27`, estado Running |
| Tipo de identidad | SystemAssigned; representación Entra ManagedIdentity habilitada |
| Principal / Object ID | `076d1b24-1406-43bc-a477-922d03cb5cc0` |
| App / Client ID | `40aeffea-62db-4902-bf97-b5c607b653ad` |
| Tenant | `cddb6514-1d4f-4a4a-a3ed-a1ca3de10829` |
| Credencial configurada y cadena ejecutada | Únicamente `ManagedIdentityCredential` |
| Selección de identidad de usuario | No hay `AZURE_CLIENT_ID` configurado; se usa la identidad del sistema |
| Scope solicitado | `https://api.fabric.microsoft.com/.default` |
| Audiencia observada en el token | `https://api.fabric.microsoft.com` |
| IDs observados en el token | Coinciden con Principal ID, App ID y tenant anteriores |
| Endpoint desplegado | HTTPS; coincide exactamente con el endpoint local que funciona |
| Workspace de destino | `1873b863-b221-43a0-9522-aed836c91536` |
| API de destino | `7ef769b0-d967-4e06-9b6b-5d97d2c8853f` |

Los metadatos del token se decodificaron para diagnóstico. No se guardó ni se imprimió el token; esa decodificación no se utiliza para autorizar usuarios ni reemplaza la validación que realiza Fabric.

## Pruebas realizadas

| Ejecución | Consulta o control | Resultado |
| --- | --- | --- |
| Cuenta de desarrollo local | Consulta completa, con el mismo código, scope y endpoint; identidad de usuario Azure CLI | 200 y nueve colecciones |
| MI desde la Function en Azure | `query { __typename }` | 403 |
| MI desde la Function en Azure | `query { vw_kpi_epres(first: 1) { items { Metrica } } }` | 403; lectura no confirmada |
| API pública en Azure | GET `/api/datos/epre`, consulta completa | 503; dependencia de datos 403 en Application Insights |
| API pública sin clave / clave inválida | Autenticación del consumidor | 401 / 401 |
| API pública con parámetro no admitido | Validación del contrato HTTP | 400 |
| Sonda administrativa sin clave / con clave ordinaria | Restricción a administradores | 401 / 401 |
| Pruebas automatizadas | Suite completa del proyecto | 36 correctas |
| Dependencias Python | `pip check` | Sin incompatibilidades |

La ejecución administrativa terminó correctamente y devolvió un informe, pero `data_read_succeeded=false`. Su HTTP 200 **no** representa una lectura exitosa del origen: los dos resultados internos fueron 403. Fabric no entregó un código de error adicional dentro de los campos JSON inspeccionados.

## Correlación

- Diagnóstico mínimo: request ID `84a30b3c-de75-4fac-99f1-3ff916650a00`; trace ID `63d797ab84a84c1c9b2d97603bacfb35`.
- Consulta pública completa: request ID `ec9685c7-604b-4376-a390-0bc3beae3267`; trace ID `fd9c595628a54aa5a56d4377ff4b8057`.
- Los reportes locales contienen sólo metadatos: `dist/fabric-setup-audit.json`, `dist/fabric-identity-diagnostic.json`, `dist/azure-verification.json` y `dist/azure-telemetry.json`.

## Repetir las comprobaciones

```powershell
.\.venv\Scripts\python.exe -m scripts.check_fabric_identity
.\.venv\Scripts\python.exe -m scripts.check_azure
.\deploy\check-telemetry.ps1
```

La sonda está en `POST /api/internal/diagnostics/data-access` y exige autenticación ADMIN del host. El script obtiene las claves sólo en memoria. Ejecuta únicamente dos consultas fijas y devuelve estados, códigos sanitizados y cantidades; no acepta consultas del consumidor ni devuelve registros de negocio.

## Alcance de la conclusión

La revisión respalda que se está utilizando la MI correcta contra la API correcta con el scope y el formato documentados. Para identificar la autorización que falta o no se aplica, el administrador de Fabric debe revisar los permisos efectivos del Principal ID indicado, el alcance del rol `funcpiepre`, la autorización de ejecución de la API, la configuración de tenant y, según el modo de conexión, el acceso a la fuente. Ninguna de esas causas concretas queda demostrada únicamente por un HTTP 403.

Referencias contrastadas: [conexión y permisos de GraphQL](https://learn.microsoft.com/en-us/fabric/data-engineering/connect-apps-api-graphql), [autenticación de servicio y scope](https://learn.microsoft.com/en-us/fabric/data-engineering/api-graphql-service-principal), [selección de credencial de Azure Identity](https://learn.microsoft.com/en-us/azure/developer/python/sdk/authentication/credential-chains#use-a-specific-credential).
