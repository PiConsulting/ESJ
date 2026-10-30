# Entorno Azure de desarrollo

Creado el 27/10/2026 con autorización del usuario, en el grupo indicado. Estado: infraestructura y código publicados; Fabric rechaza con HTTP 403 a la identidad administrada nueva. La API traduce ese fallo a 503. El acceso con la cuenta personal local no concede acceso a la Function.

## Recursos

Suscripción: `Azure subscription 4` (`d468911c-54a0-4a0b-af8c-938d81aeea30`). Tenant: Naturgy Argentina (`cddb6514-1d4f-4a4a-a3ed-a1ca3de10829`). Grupo: `rg-pi-epre-dev`. Región: `eastus`, heredada del grupo y validada como compatible con Flex Consumption.

| Recurso | Nombre |
| --- | --- |
| Function App | `func-pi-epre-dev-hnso27` |
| Plan Flex Consumption FC1 | `asp-pi-epre-dev` |
| Storage Standard LRS | `steprehnso27` |
| Application Insights | `appi-pi-epre-dev` |
| Log Analytics | `law-pi-epre-dev` |

Endpoint REST: `https://func-pi-epre-dev-hnso27.azurewebsites.net/api/datos/epre`.

Managed Identity del sistema, Object ID: `076d1b24-1406-43bc-a477-922d03cb5cc0`.

Application Insights App ID: `4686bd1a-a9e0-4e8f-9eee-7715806f1d12`.

La Function usa Linux, Python 3.12, 2048 MB y un máximo de 40 instancias; no hay instancias always ready. Los 40 son el límite de escalado, no instancias ejecutándose permanentemente. Functions, almacenamiento y telemetría generan cargos según uso; esta configuración no constituye un presupuesto ni una garantía de costo cero. Logs: retención 30 días y cuota diaria de ingesta de 1 GB en Log Analytics. La cuota puede interrumpir la visibilidad y no es un límite monetario estricto.

## Autenticación y permisos

- Consumidor → Function: HTTPS y Function Key en `x-functions-key`. No pasar claves por URL, chat o archivos de código.
- Function → Fabric: `ManagedIdentityCredential`, usando la identidad del sistema. No se copia la sesión Azure CLI ni un token personal.
- Function → Storage: conexión estándar con clave de la cuenta nueva, calculada dentro de ARM y almacenada en los app settings. La cuenta que despliega puede crear recursos, pero sus permisos excluyen `Microsoft.Authorization/*/Write`; por eso no puede conceder el rol de datos necesario para usar identidad en Storage. La clave de almacenamiento no se imprime ni guarda localmente.
- Storage exige HTTPS/TLS 1.2 y no admite blobs públicos. Sus endpoints de red son públicos y requieren autenticación; no se creó una red privada ni un Private Endpoint.
- Publicación por Azure CLI/Entra; FTP y credenciales básicas SCM están deshabilitados.

Para migrar también Storage a identidad, un administrador deberá asignar `Storage Blob Data Owner` a la identidad de la Function sobre esta cuenta para las necesidades del host HTTP. Ese rol también cubre el contenedor de despliegue. Después cambiar `AzureWebJobsStorage` por `AzureWebJobsStorage__accountName`, la autenticación del contenedor a `SystemAssignedIdentity` y deshabilitar el acceso con claves. Revisar roles adicionales si se agregan triggers diferentes. No hacer el cambio de configuración antes de conceder los permisos.

## Pendiente en Fabric

La cuenta de despliegue obtiene `InsufficientWorkspaceRole` al consultar la administración de roles del workspace. El código desplegado sí obtiene un token con Managed Identity, pero GraphQL devuelve 403. Se necesita la intervención de un administrador de Fabric.

Solicitud lista para copiar: [solicitud-acceso-fabric.txt](solicitud-acceso-fabric.txt). Pide Execute sobre la API y lectura de las fuentes que correspondan. No se asignó Contributor sobre todo el workspace.

La [validación ampliada del 29/10/2026](validacion-acceso-mi-2026-10-29.md) confirmó la identidad real del token, el endpoint, tenant y scope. La cuenta de desarrollo puede ejecutar la consulta completa; desde Azure la MI recibe 403 incluso para una única lectura de KPI. La sonda administrativa y el procedimiento para repetirla se describen en ese informe. La suite actual tiene 36 pruebas correctas.

## Reproducir y verificar

Verificación real del 27/10/2026, después de publicar la corrección de `host.json`:

| Comprobación | Resultado |
| --- | --- |
| Sin clave / clave inválida | 401 / 401 |
| Clave válida y parámetro no admitido | 400 |
| Clave válida y consulta real | 503; Fabric devuelve 403 |
| Correlación W3C en respuestas 400 y 503 | Coincide con el `traceparent` enviado |
| Ingesta en Application Insights | Confirmada: request, logs y spans de la misma operación |
| Pruebas del proyecto | 30 correctas |

Operación de referencia: `7b0cfd1e6f7b452a802de468aa9e70ed`, request ID `4210f557-9db1-430b-a724-ac70f32bb2df`. En Insights tiene un request del host con `resultCode=503`, ocho eventos propios y tres dependencias: `esj.request`, `fabric.token` correcto y `fabric.graphql` fallido con 403. No hay dependencias Fabric duplicadas. La ingesta del host demoró varios minutos respecto del exportador de spans.

El host marca esta invocación manejada como `success=true` aunque el HTTP sea 503. Para monitorear fallos de negocio/HTTP usar `resultCode`, el evento `request.completed` y el estado de la dependencia; no basarse sólo en el éxito de la invocación. Las consultas KQL incluidas ya usan el estado HTTP de nuestros eventos.

Desde la raíz, con Azure CLI autenticado en Naturgy:

```powershell
.\deploy\deploy.ps1 -Action validate
.\deploy\deploy.ps1 -Action provision
.\deploy\deploy.ps1 -Action publish
.\.venv\Scripts\python.exe -m scripts.check_azure
.\deploy\check-telemetry.ps1
```

`provision` valida y aplica la plantilla ARM en modo incremental. Reaplicarla restaura su configuración declarada, incluidos los app settings; revisar primero si hubo cambios manuales. Los nombres se derivan de forma determinista del ID del grupo. El endpoint Fabric se toma de `local.settings.json`, se pasa como parámetro seguro y el archivo temporal se elimina al finalizar. Las salidas ARM no contienen claves ni connection strings.

`publish` regenera el ZIP con las fuentes y solicita build remoto Linux. No publica configuración local, datos de negocio ni `.venv`.

`check_azure` recupera una Function Key sólo en memoria y comprueba 401 sin clave, 401 con clave inválida, 400 por parámetro no admitido y 200 al consultar Fabric. Verifica también los headers de correlación. Devuelve salida distinta de cero si un caso falla; no se debe considerar completo mientras la lectura real no dé 200. Guarda sólo metadatos en `dist/azure-verification.json`.

`check-telemetry.ps1` consulta Application Insights mediante su API y guarda metadatos seleccionados en `dist/azure-telemetry.json`. No guarda respuestas de negocio, cuerpos HTTP, claves ni headers de autenticación. Esperar la ingesta antes de comparar las solicitudes con los eventos. Las consultas de operación habituales están en `docs/application-insights.kql`.

Referencias: [Flex Consumption y publicación](https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-how-to), [configuración de correlación HTTP](https://learn.microsoft.com/en-us/azure/azure-functions/functions-host-json#applicationinsightshttpautocollectionoptions), [autenticación de aplicaciones en GraphQL](https://learn.microsoft.com/en-us/fabric/data-engineering/connect-apps-api-graphql).
