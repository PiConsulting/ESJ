# API ESJ → Fabric

Azure Functions Python v2. La aplicación consulta **Fabric real** con la consulta entregada por el analista. No hay modo dummy ni datos de ejemplo en ejecución.

El entorno de desarrollo está desplegado en `rg-pi-epre-dev`, East US, con Function, identidad administrada y Application Insights. **Falta habilitar esa identidad en Fabric: actualmente GraphQL devuelve 403 y la API responde 503.** Ver [recursos y comandos de despliegue](docs/azure-dev.md) y [guía de observabilidad](docs/azure-functions.md).

## Qué hace ahora

```text
Postman / EPRE → GET /api/datos/epre
              → token Microsoft Entra
              → POST al endpoint GraphQL de Fabric
              → JSON con KPI, demanda/generación, mensajes, reclamos y setas (incluyendo GPS)
```

El endpoint de Fabric es la dirección a la que se envía una consulta. La consulta indica qué colección y campos pedir; no define por sí sola cómo están construidas las tablas Gold ni el significado de sus campos.

La consulta exacta está en [esj_api/queries/epre.graphql](esj_api/queries/epre.graphql). Pide nueve colecciones en una sola llamada a Fabric:

| Colección | Campos |
| --- | --- |
| `vw_kpi_epres` | `Metrica`, `Fecha`, `Hora`, `Valor` |
| `demanda_Generacions` | `Fecha_Hora`, `Demanda`, `Generacion` |
| `carteros` | `fecha_hora`, `mensaje` |
| `cantidad_Reclamos` | `Fecha_Hora`, `Reclamos_Totales` |
| `reclamos` | `Fecha_Hora`, `Descripcion`, `Reclamo`, `Suministro`, `Tipo` |
| `vw_reclamos_gps` | `Tipo`, `Latitud`, `Longitud`, `Suministro`, `Reclamo`, `Fecha_Hora`, `Descripcion` |
| `cantidad_Setas_Afectadas` | `Fecha_Hora`, `Cantidad` |
| `setas_Afectadas` | `Fecha_Hora`, `ET_Distribuidor`, `ID_Seta`, `Usuarios_Afectados` |
| `vw_setas_afectadas_gps` | `Tipo`, `Latitud`, `Longitud`, `ET_Distribuidor`, `Setas`, `Fecha_Hora`, `Descripcion` |

La consulta actual no incluye agentes telefónicos ni límites `first: 10`. No aplica filtros, orden ni paginación. Omitir `first` **no garantiza recibir todos los registros**: se aplica el comportamiento por defecto de Fabric. No se recorren páginas adicionales ni se dispone de un indicador de fin de datos con esta consulta.

La respuesta pública contiene `origen: "fabric"` y `colecciones`. Dentro de `colecciones` se mantienen las nueve claves originales de Fabric. Cada una tiene `cantidad_devuelta`, `limite_solicitado` e `items` con sólo los campos pedidos. `limite_solicitado` vale `null` en todas porque no se pide un límite explícito. La cantidad es el tamaño de la lista recibida, **no el total de la tabla o vista**.

Se preservan valores, tipos JSON, fechas, horas, precisión textual, duplicados, orden y nulos recibidos, sin agrupar ni calcular indicadores. Una colección vacía real devuelve su lista vacía; una colección o campo ausente produce 502. Si GraphQL devuelve errores o datos parciales, se rechaza la respuesta completa.

## Cambios respecto de la primera versión

- El endpoint actual es **`/api/datos/epre`**. Reemplaza las rutas anteriores `/api/datos/resumen` y `/api/datos/setas-afectadas`; actualizar Postman.
- Se eliminaron `DummyProvider`, sus cinco registros, el selector `DATA_PROVIDER`, los modelos ficticios y los conteos por región.
- Se eliminaron `providers.py` y `validation.py`, sustituidos por la integración concreta en `fabric.py` y la validación mínima en la entrada HTTP.
- Ya no se aceptan `desde`, `hasta` ni `region`: la consulta recibida no los implementa. Enviarlos devuelve 400 para evitar aparentar que se aplicó un filtro. El body debe estar vacío. Sólo se permite el parámetro reservado `code` de Azure Functions; para la clave se recomienda el header `x-functions-key`.
- Los únicos datos simulados están en tests automatizados, donde evitan consultar Fabric con cada prueba.

El JSON público sigue siendo provisional hasta que EPRE y el analista confirmen el contrato.

## Configuración local

Requisitos: Python 3.12, Azure CLI, Azure Functions Core Tools v4 y Azurite.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Si ya existe `.venv`, sólo necesitás instalar las dependencias. Si `python` abre Microsoft Store, usá la ruta a un intérprete Python instalado.

En esta carpeta ya está configurado el endpoint recibido en **`local.settings.json`**, excluido de Git y publicación. No se guarda en el código ni en esta documentación. Para una copia nueva del proyecto, sólo si ese archivo no existe:

```powershell
Copy-Item local.settings.example.json local.settings.json
```

Completar `FABRIC_GRAPHQL_ENDPOINT` y usar `AZURE_TOKEN_CREDENTIALS=AzureCliCredential` para desarrollo. El resto de los valores locales viene en la plantilla.

Iniciar sesión con una cuenta del tenant que tiene acceso a Fabric:

```powershell
az login --tenant <TENANT_ID_DE_FABRIC> --allow-no-subscriptions
```

Reemplazar el marcador por el tenant confirmado por el administrador; no se puede deducir a partir del workspace de la URL. Si ya hay una sesión válida en el tenant correcto, no hace falta repetir el login. No copiar tokens a archivos de configuración.

Para este proyecto, la cuenta de desarrollo debe ser la cuenta corporativa de **Naturgy** indicada por el equipo. No usar la sesión de PI Consulting para validar el acceso a este Fabric. La cuenta de desarrollo sólo se usa en pruebas locales; no es la identidad prevista para producción.

## Prueba real sin levantar el servidor

```powershell
.\.venv\Scripts\python.exe -m scripts.check_fabric
```

Carga la configuración local e invoca el mismo código HTTP/proveedor que usa la Function. Obtiene un token y hace una consulta real de lectura a Fabric. Muestra estado y cantidad de registros por colección; omite sus valores. Retorna código de salida distinto de cero si falla. No valida el host ni la Function Key.

## Ejecutar y probar desde Postman

En una terminal desde la raíz:

```powershell
azurite.cmd --silent --disableTelemetry --location .azurite
```

En otra terminal:

```powershell
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"
func.cmd start
```

En Postman: **GET**, URL `http://localhost:7071/api/datos/epre`, **Params vacío** y **Body → none**.

```powershell
curl.exe -i "http://localhost:7071/api/datos/epre"
```

Una consulta con `?region=norte` debe devolver 400. Las rutas anteriores `/api/datos/resumen` y `/api/datos/setas-afectadas` ya no existen. Detener cada proceso con Ctrl+C. Reiniciar el host tras cambiar configuración o identidad, porque la credencial se reutiliza dentro del proceso.

## Autenticación: dos accesos diferentes

**EPRE → Function:** se conserva `AuthLevel.FUNCTION`. En Azure enviar `x-functions-key: {{function_key}}` desde una variable privada de Postman. La clave la valida Azure Functions, no se envía a Fabric. Se verificó el rechazo 401 sin clave y con clave inválida en Azure. Core Tools local normalmente no exige la clave.

**Function → Fabric:** `DefaultAzureCredential` pide un token para `https://api.fabric.microsoft.com/.default` y lo envía como Bearer. El SDK gestiona la vigencia del token. `AZURE_TOKEN_CREDENTIALS` restringe explícitamente la identidad:

Ésta es la arquitectura solicitada desde el inicio: habilitar una Managed Identity en la Function App y autorizar **esa identidad** en Fabric. Azure administra sus credenciales, sin contraseña personal ni secretos de aplicación en el código. La identidad ya está habilitada y obtiene tokens en Azure; está pendiente concederle acceso a Fabric. Los identificadores y la solicitud para el administrador están en [la documentación del entorno](docs/azure-dev.md).

| Entorno | Valor |
| --- | --- |
| Desarrollo local con `az login` | `AzureCliCredential` |
| Azure con identidad administrada habilitada | `ManagedIdentityCredential` |

Para una identidad administrada asignada por el usuario, configurar también `AZURE_CLIENT_ID` con su client ID. La identidad necesita permisos **Run Queries and Mutations / Execute** sobre la API GraphQL y los permisos correspondientes sobre la fuente de datos según su modo de conexión. La Function Key no concede esos permisos.

Referencias: [autenticación y permisos de GraphQL](https://learn.microsoft.com/en-us/fabric/data-engineering/connect-apps-api-graphql), [scope de Fabric](https://learn.microsoft.com/en-us/fabric/data-engineering/api-graphql-service-principal) y [selección de credenciales en DefaultAzureCredential](https://learn.microsoft.com/en-us/azure/developer/python/sdk/authentication/credential-chains).

## Errores y diagnóstico

| HTTP | Código público | Causa |
| --- | --- | --- |
| 400 | `solicitud_invalida` | Body no vacío o parámetros de datos no admitidos. |
| 502 | `error_proveedor` | Error de red, HTTP inesperado, JSON inválido, errores GraphQL o estructura incompatible. |
| 503 | `servicio_no_disponible` | Configuración, obtención del token, rechazo 401/403 de Fabric, HTTP 429/503. |
| 504 | `tiempo_agotado` | Timeout de conexión o lectura de Fabric. |
| 500 | `error_interno` | Falla inesperada. |

Los errores mantienen `{ "error": { "codigo": "...", "mensaje": "...", "request_id": "..." } }`. Todas las respuestas propias incluyen `X-Request-ID`, `X-Trace-ID` y `Cache-Control: no-store`. Se registran eventos JSON de inicio/fin, errores, duración, token, consulta a Fabric y cantidades por colección. La correlación usa el contexto de Azure Functions; los spans manuales se exportan cuando está configurado `APPLICATIONINSIGHTS_CONNECTION_STRING`.

Se descartan datos parciales si GraphQL devuelve `errors`, incluso con HTTP 200. No se registran tokens, Function Keys, cuerpos de Fabric ni valores de registros. Las excepciones externas no se copian al consumidor. No se siguen redirecciones. El POST tiene timeout de conexión de 5 segundos y de lectura de 30 segundos; no hay reintentos automáticos. Los errores del host, como una Function Key inválida o una ruta desconocida, pueden usar otro formato.

Ante 503: revisar primero el log asociado. Si falló el token, comprobar `az login` y tenant o la identidad administrada. Si Fabric devolvió 401/403, pedir revisión de permisos sobre la API y la fuente. Ante un error GraphQL, pedir al analista ejecutar la misma consulta con la identidad de integración y verificar esquema y permisos. Nunca corregir esto agregando una respuesta inventada.

Validación del 27/10/2026: pasaron **30 pruebas automatizadas**, incluyendo consulta de nueve colecciones, correlación de logs/spans, privacidad, concurrencia y exclusiones del paquete de despliegue. En Azure se verificaron la Function Key, el 400 de validación y los headers de correlación. La prueba de datos aún falla: Fabric rechaza la identidad administrada con 403. El código maneja ese rechazo con 503 y diagnóstico en Application Insights.

La prueba real con Naturgy y la llamada HTTP a la Function local respondieron **200**. Cantidades observadas: KPI 7; demanda/generación 100; carteros 100; cantidad de reclamos 100; reclamos 100; reclamos GPS 9; cantidad de setas afectadas 100; setas afectadas 30; setas GPS 30. Estas cantidades no prueban que se hayan recibido todos los registros disponibles. Se actualizó `respuesta_epre.txt` con el JSON real, excluido de Git y publicación. `pip check` no detectó incompatibilidades.

## Archivos

| Archivo | Responsabilidad |
| --- | --- |
| `function_app.py` | GET, rechazo de filtros no implementados y errores JSON. |
| `esj_api/config.py` | Ruta, endpoint HTTPS de Fabric y selección explícita de credencial. |
| `esj_api/fabric.py` | Token, POST real, timeouts y validación de respuesta GraphQL. |
| `esj_api/queries/epre.graphql` | Consulta recibida del analista con las nueve colecciones. Se incluye en publicación. |
| `esj_api/transform.py` | Selección de campos públicos y cantidades por colección. |
| `esj_api/telemetry.py` | Correlación W3C, logs JSON y spans manuales hacia Azure Monitor. |
| `tests/test_fabric.py` | Pruebas sin red, con respuestas controladas. |
| `scripts/check_fabric.py` | Comprobación real usando configuración local. |
| `scripts/check_azure.py` | Pruebas HTTP contra Azure; utiliza la Function Key en memoria y sólo guarda metadatos. |
| `scripts/package_function.py` | Paquete ZIP de fuentes, sin configuración local ni datos. |
| `docs/azure-functions.md`, `docs/application-insights.kql` | Preparación de Azure y consultas de diagnóstico. |
| `deploy/azure-settings.example.json` | Plantilla de configuración para Azure, sin valores reales. |
| `deploy/azuredeploy.json`, `deploy/deploy.ps1` | Infraestructura ARM, validación, creación y publicación con build remoto. |
| `deploy/check-telemetry.ps1` | Consulta acotada de metadatos de Application Insights. |
| `docs/azure-dev.md`, `docs/solicitud-acceso-fabric.txt` | Recursos reales y acceso pendiente de la identidad en Fabric. |
| `host.json`, `requirements.txt` | Configuración del host y dependencias. |
| `local.settings.example.json` | Plantilla sin endpoint privado ni tokens. |
| `.gitignore`, `.funcignore` | Exclusiones de Git/publicación. |

## Pruebas automatizadas

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Cubren el flujo HTTP → token → POST → transformación, datos vacíos/nulos, campos privados, filtros retirados, configuración, errores de autenticación y permisos, errores GraphQL con datos parciales, límites, JSON inválido y timeouts. No prueban permisos reales ni la Function Key de Azure.

## Diagnóstico administrativo de la identidad

`POST /api/internal/diagnostics/data-access` exige `AuthLevel.ADMIN`: la clave de consumidor habitual no autoriza esa ruta. Comprueba la visibilidad del workspace mediante la API REST de Fabric y ejecuta dos sondas GraphQL fijas desde la identidad configurada en la Function: `__typename` y `vw_kpi_epres(first: 1) { items { Metrica } }`. Devuelve sólo estados, cantidades, identificadores de la identidad y códigos de error sanitizados. No acepta consultas del cliente ni devuelve filas, descripciones del workspace o tokens.

```powershell
.\.venv\Scripts\python.exe -m scripts.check_fabric_identity
```

El script obtiene la clave administrativa sólo en memoria, verifica el rechazo sin clave y con una clave ordinaria, y guarda metadatos en `dist/fabric-identity-diagnostic.json`. Un HTTP 200 de esta ruta indica que se ejecutó el diagnóstico; la lectura sólo se confirma con `data_read_succeeded=true`. El token se decodifica únicamente para mostrar metadatos, sin utilizar esa decodificación como validación de seguridad. La validación del token y sus permisos corresponde al servicio externo.

El diagnóstico compara también la API original con la API alternativa `6165c71e-2e6b-4806-8dac-dbe8ba35e1c2`, entregada para investigar el acceso con MI. Usa exactamente el mismo token y la misma consulta `vw_kpi_epres(first: 10) { items { Metrica } }` en ambos destinos. No modifica `FABRIC_GRAPHQL_ENDPOINT` ni la consulta pública de nueve colecciones.

```powershell
.\.venv\Scripts\python.exe -m scripts.check_fabric_identity --target alternate
```

Las pruebas automatizadas del diagnóstico comprueban su autenticación declarada, las consultas fijas, la comparación de ambos destinos, el tratamiento de HTTP 403/errores GraphQL y la exclusión de filas, mensajes y tokens. La ejecución completa incluye 40 pruebas.

## Lo que falta acordar con el analista

1. Semántica y unidades de los indicadores, zona horaria de fechas/horas, significado de cada campo y tratamiento de coordenadas/nulos.
2. Qué otros campos necesita EPRE y cómo quedará el JSON público.
3. Cómo filtrar fechas/regiones, si corresponde, con las consultas y variables validadas.
4. Cómo recorrer páginas y definir orden estable para las nueve colecciones, y qué volumen necesita EPRE.
5. Permisos de la Managed Identity creada sobre la API y las fuentes de Fabric.

El despliegue de desarrollo está realizado. No se cambiaron permisos del workspace Fabric.
