# Preparar y verificar la Function en Azure

El proyecto ya es una Azure Function Python v2 con trigger HTTP y `AuthLevel.FUNCTION`.
El entorno de desarrollo ya fue creado y el código se publicó el 27/10/2026 en `rg-pi-epre-dev`. **El acceso a datos está pendiente de habilitar la identidad administrada en Fabric**. Ver [recursos, comprobaciones y comandos del entorno](azure-dev.md).

## Referencia de arquitectura del proyecto

Referencia compartida por el usuario el 27/10/2026: [La Data Web — Exponer datos de un Lakehouse mediante API REST](https://blog.ladataweb.com.ar/exponer-datos-lakehouse-api-graphql/) (artículo del 7/10/2026). Describe una prueba de arquitectura, no una especificación completa del contrato EPRE ni de las políticas Azure del cliente.

El flujo adoptado es **EPRE → REST de Azure Functions → API for GraphQL de Fabric → datos Gold**. El consumidor externo recibe únicamente el endpoint REST y su Function Key; no necesita permisos directos sobre el workspace, Lakehouse ni SQL endpoint.

| Punto de la referencia | Aplicación en ESJ |
| --- | --- |
| Function App en Flex Consumption | Creada en East US, región del grupo indicado por el usuario. Python 3.12 y sin instancias always ready. |
| Managed Identity asignada por el sistema | Habilitada en la Function App. El acceso a Fabric usa esta identidad, sin secretos de aplicación ni cuenta personal. |
| `DefaultAzureCredential` y scope Fabric | Ya implementados; en Azure se restringe explícitamente a `ManagedIdentityCredential`. |
| Endpoint en `FABRIC_GRAPHQL_ENDPOINT` | Ya implementado; se configura por entorno. |
| Permisos de la identidad en Fabric | Pendientes. El artículo usa Contributor en el workspace y contempla permisos granulares. Acordar los permisos necesarios sobre la API y la fuente con el administrador; no asignar Contributor automáticamente. |
| REST protegido con `AuthLevel.FUNCTION` | Validado en Azure: llamadas sin clave o con clave inválida reciben 401. Usamos el header `x-functions-key`. |
| Transformar GraphQL a JSON público | Ya implementado con selección de campos y metadatos por colección; contrato EPRE todavía provisional. |

La demo del artículo usa exportaciones, filtros de fecha, agrupaciones y un precio promedio. Esas reglas pertenecen a su ejemplo: ESJ utiliza las nueve colecciones y los campos entregados por el analista. No se incorporan filtros, agregaciones o cálculos adicionales sin una definición del proyecto. La consulta actual tampoco recorre páginas adicionales.

Application Insights, trazabilidad y empaquetado son ampliaciones solicitadas expresamente por el usuario durante este proyecto; no reemplazan el flujo del artículo. El usuario autorizó el despliegue de desarrollo y la elección de los valores restantes. Los valores aplicados y sus limitaciones se documentan en `azure-dev.md`. Las políticas y presupuesto de producción deberán definirse antes de promover este entorno.

## Qué registra

Todos los eventos propios son JSON en `message`, mediante el logger nativo de Functions:

| Evento | Contenido |
| --- | --- |
| `request.started` | Ruta fija y método. |
| `request.completed` | HTTP final y duración total en milisegundos. |
| `request.rejected` | Código de validación para una petición inválida. |
| `request.failed` | Código de error y diagnóstico estático; errores inesperados incluyen tipo y ubicaciones del stack. |
| `operation.started/completed/failed` | Inicio, duración o fallo de `fabric.token` y `fabric.graphql`. |
| `fabric.response` | Estado HTTP del servicio externo. |
| `fabric.results` | Cantidad de filas por colección, sin sus valores. |

Cada evento incluye `request_id`, `invocation_id`, `trace_id` y `span_id`. La respuesta HTTP devuelve `X-Request-ID` y `X-Trace-ID`. El JSON público de datos y errores conserva su contrato.

`request_id` identifica nuestra llamada; `invocation_id` identifica la ejecución en Functions; `trace_id` permite seguir el recorrido distribuido y corresponde al `operation_Id` en Application Insights. El contexto W3C que entrega Functions se usa como padre de los spans; al llamar a Fabric se envía `traceparent`. Esto no garantiza que Fabric exponga su telemetría interna, pero permite correlacionar todo lo instrumentado por nuestra aplicación.

## Una sola fuente por tipo de telemetría

```text
Host Azure Functions → request HTTP y logs Python → Application Insights
Worker Python        → spans manuales             → Application Insights
                          esj.request (interno)
                            ├─ fabric.token (interno)
                            └─ fabric.graphql (cliente HTTP)
```

El host conserva su integración nativa. Sólo usamos `AzureMonitorTraceExporter` para spans manuales; no hay un segundo exportador de logs ni autoinstrumentación de `requests`/Azure Identity. `host.json` desactiva la detección automática de dependencias para evitar duplicados y mantiene la colección de solicitudes. Los spans internos también pueden aparecer en `dependencies`; no son requests HTTP adicionales.

El exportador puede mostrar la dependencia como `POST /`, con destino lógico `fabric-graphql`. Para identificarla de forma estable usar `customDimensions["app.operation"] == "fabric.graphql"`, como en las consultas KQL incluidas. No usamos la URL privada como destino visible.

No configurar `telemetryMode=OpenTelemetry`, `PYTHON_APPLICATIONINSIGHTS_ENABLE_TELEMETRY=true` ni un distro adicional sobre esta implementación. Son otras formas de configurar la instrumentación; requieren migrar el pipeline completo, no sumar flags. La plantilla deja los flags de autoinstrumentación en `false`.

El exportador de Microsoft seleccionado tiene versión prerelease `1.0.0b58`; está fijada en `requirements.txt`. Se eligió la configuración explícita para controlar qué datos se exportan y conservar los logs nativos del host. Revisar sus actualizaciones antes del despliegue productivo.

## Configuración para Azure

Para reproducir el entorno, usar `deploy/azuredeploy.json` y `deploy/deploy.ps1`, como indica `azure-dev.md`. La configuración equivalente en el portal es:

1. Una **Function App Linux, Python 3.12, runtime Functions 4**, tomando **Flex Consumption** como plan de referencia del artículo y confirmando región/políticas antes de crearla. El modelo Python v2 es el del código, distinto del número del runtime.
2. La cuenta de almacenamiento requerida por Functions y la configuración de acceso que corresponda al plan. No usar `UseDevelopmentStorage=true` en Azure.
3. **Application Insights basado en un workspace de Log Analytics**, asociado a la Function App.
4. **Managed Identity asignada por el sistema** en la Function App; conceder a esa identidad los permisos necesarios en GraphQL y su fuente de datos. Confirmar el mismo tenant para el acceso directo previsto.

Tomar como referencia `deploy/azure-settings.example.json`. Reemplazar los marcadores al cargar las variables en el portal; no publicar el archivo de ejemplo como settings reales.

| Variable | Valor en Azure |
| --- | --- |
| `FABRIC_GRAPHQL_ENDPOINT` | Endpoint real del entorno. |
| `AZURE_TOKEN_CREDENTIALS` | `ManagedIdentityCredential`. |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Connection string del Application Insights asociado; la usan host y exportador. |
| `AZURE_CLIENT_ID` | Omitir para la identidad asignada por el sistema prevista. Sólo aplica si se acuerda cambiar a una identidad asignada por el usuario. |
| `PYTHON_APPLICATIONINSIGHTS_ENABLE_TELEMETRY` | `false`. |
| `PYTHON_ENABLE_OPENTELEMETRY` | `false`. |

El portal configura los valores propios del runtime y del almacenamiento según el plan. En planes que utilizan app settings para ello, comprobar `FUNCTIONS_WORKER_RUNTIME=python`, `FUNCTIONS_EXTENSION_VERSION=~4` y `AzureWebJobsStorage` o sus opciones de identidad. En Flex Consumption el runtime se configura en `functionAppConfig`, por lo que no hay que copiar ciegamente settings de otro plan.

La cuenta Azure CLI de Naturgy sólo se usa localmente. La aplicación rechaza esa selección de credenciales al detectar `WEBSITE_INSTANCE_ID` o `CONTAINER_NAME`, los indicadores de Azure usados por el worker Python; Core Tools sigue admitiéndola.

Si la política del recurso de Insights requiere exclusivamente autenticación Entra para ingestión, hay que configurar también la autenticación del host y el exportador para esa política; el ejemplo actual usa la conexión estándar de Application Insights. Esto es independiente de la identidad usada para leer Fabric.

## Niveles, muestreo y privacidad

`host.json` habilita eventos propios, `Host.Results` y `Host.Aggregator` en Information. El resto queda en Warning. El muestreo del host está desactivado para esta validación inicial. Los spans respetan la decisión de muestreo del padre W3C y, sin padre, se registran todos. Revisar el volumen y definir una política consistente entre host y worker antes de producción.

No se registran tokens, claves, headers completos, queries GraphQL, endpoint privado, mensajes de GraphQL, filas, suministros, coordenadas ni descripciones de reclamos. Las excepciones de terceros no se serializan como eventos OpenTelemetry. Los errores manejados se consultan en `traces` y en dependencias fallidas; no se promete una fila en `exceptions` para cada error controlado.

Se habilita `enableHttpTriggerExtendedInfoCollection` para que el host registre método, URL y resultado HTTP y respete la correlación W3C entrante. Desactivarlo hizo que Azure registrara `resultCode=0` y perdiera la correlación entrante en algunas solicitudes; no basta con habilitar `enableW3CDistributedTracing` por separado. Enviar siempre la Function Key por `x-functions-key`, sin agregar secretos ni datos personales a la URL. La telemetría propia excluye los parámetros; la colección HTTP del host puede incluirlos. No se registran cuerpos ni headers completos desde nuestra aplicación.

La exportación de spans usa un buffer en memoria y envío por lotes. No bloquea cada respuesta esperando a Insights ni persiste telemetría en disco. Una caída del proceso o una interrupción prolongada de ingestión puede perder spans pendientes. Logs de inicio/cold start pueden no tener los IDs de una petición concreta.

## Verificar localmente

Sin `APPLICATIONINSIGHTS_CONNECTION_STRING`, se generan logs y trazas para correlación local, pero no se envían spans a Azure. Los logs en consola del host incluyen el JSON de cada evento.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Levantar Azurite y Core Tools como indica el README. Enviar un contexto W3C de prueba:

```powershell
curl.exe -i -H "traceparent: 00-1234567890abcdef1234567890abcdef-1234567890abcdef-01" "http://localhost:7071/api/datos/epre"
```

Esperar `X-Trace-ID: 1234567890abcdef1234567890abcdef` y el mismo ID en los eventos. `invocation_id` aparece al ejecutar con Core Tools/Azure; es nulo al llamar directamente con el script de diagnóstico. No habilitar debug de credenciales ni copiar tokens para investigar.

## Paquete y publicación

Generar el paquete revisable:

```powershell
.\.venv\Scripts\python.exe -m scripts.package_function
```

Produce `dist/esj-function.zip` con `function_app.py`, `host.json`, `requirements.txt` y el paquete `esj_api`, incluyendo `.graphql`. Excluye configuración local, datos TXT, credenciales, tests, docs y el entorno virtual.

El ZIP contiene fuentes: **requiere build remoto para instalar las dependencias Python**, no es un paquete autónomo con dependencias incorporadas. Una vez creada y configurada la Function App y autorizado el despliegue, publicar desde la raíz con Core Tools:

```powershell
func azure functionapp publish <NOMBRE_FUNCTION_APP> --python
```

Usar el build remoto soportado por el plan; no subir `.venv` de Windows a la Function Linux. Revisar `.funcignore` y no usar `--publish-local-settings` para evitar copiar el endpoint de desarrollo o `AzureCliCredential` al entorno de Azure.

## Validación después de publicar

1. Verificar que el host indexe `datos_epre` y que el GET con una Function Key válida funcione. Comprobar también rechazo sin clave y con clave inválida.
2. Confirmar que el acceso a Fabric funciona con Managed Identity, sin sesión personal de Azure CLI.
3. Enviar una petición con `traceparent` y guardar los headers de correlación. Comprobar 200 y un caso de validación 400.
4. En **Application Insights → Logs**, ejecutar por separado las consultas de `docs/application-insights.kql`. Esperar el tiempo de ingestión; comprobar request del host, logs y dependencia Fabric con el mismo `operation_Id`.
5. Verificar que haya un único request por llamada y una dependencia `app.operation=fabric.graphql`; revisar que no se exporten datos de negocio.
6. Recién con datos reales de tráfico, configurar alertas de fallos 5xx y latencia p95 y acordar retención/muestreo. El estado de invocación “Succeeded” puede coexistir con un error HTTP manejado: para errores de API usar el HTTP real, `request.completed` y el estado de la dependencia.

La validación local y los exportadores en memoria no demuestran ingestión en Azure. Las comprobaciones del entorno real se registran por separado en `azure-dev.md`.

Verificación local del 26/10/2026: **30 pruebas pasaron**, `pip check` sin incompatibilidades y ZIP revisado con nueve archivos de ejecución. Una petición HTTP real con `traceparent` respondió 200 y nueve colecciones; sus ocho eventos compartieron `request_id`, `invocation_id` y el `trace_id` enviado. Los tests también cubren fallos, solicitudes simultáneas, datos sensibles y ausencia de exportación sin connection string. En esa prueba local no se enviaron datos a Application Insights; el recurso se creó al día siguiente.

Referencias: [logs de Python en Functions](https://learn.microsoft.com/en-us/azure/azure-functions/functions-reference-python), [configuración de monitoring](https://learn.microsoft.com/en-us/azure/azure-functions/configure-monitoring), [exportador de Azure Monitor](https://learn.microsoft.com/en-us/python/api/overview/azure/monitor-opentelemetry-exporter-readme), [correlación con el contexto de Functions](https://learn.microsoft.com/en-us/python/api/overview/azure/monitor-opentelemetry-readme#monitoring-in-azure-functions).
