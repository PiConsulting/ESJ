param(
    [string]$AppId = '4686bd1a-a9e0-4e8f-9eee-7715806f1d12'
)
$ErrorActionPreference = 'Stop'
$distPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'dist'
New-Item -ItemType Directory -Path $distPath -Force | Out-Null
$bodyPath = Join-Path $distPath 'insights-query.json'
$query = @'
union withsource=table_name requests, dependencies, traces
| where timestamp > ago(1h)
| extend e=parse_json(message)
| where table_name != 'traces' or isnotempty(e.event)
| project timestamp, table_name, operation_Id, operation_ParentId, name, resultCode, success,
    event=tostring(e.event), reason=tostring(e.reason), http_status=tostring(e.status_code),
    upstream_status=tostring(e.upstream_status),
    trace_id=tostring(e.trace_id), request_id=tostring(e.request_id),
    app_operation=tostring(customDimensions['app.operation'])
| order by timestamp desc
| take 150
| order by timestamp asc
'@
@{query = $query; timespan = 'PT1H'} | ConvertTo-Json | Set-Content -LiteralPath $bodyPath -Encoding UTF8
try {
    $result = az rest --method post --url "https://api.applicationinsights.io/v1/apps/$AppId/query" --resource https://api.applicationinsights.io --body "@$bodyPath" --output json
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo consultar Application Insights.' }
    $parsed = $result | ConvertFrom-Json
    $table = $parsed.tables[0]
    $records = foreach ($row in $table.rows) {
        $record = [ordered]@{}
        for ($i = 0; $i -lt $table.columns.Count; $i++) {
            $record[$table.columns[$i].name] = $row[$i]
        }
        [pscustomobject]$record
    }
    $records | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $distPath 'azure-telemetry.json') -Encoding UTF8
    $records | Group-Object table_name | Select-Object Name, Count | Format-Table -AutoSize
    $records | Where-Object { $_.event -eq 'request.failed' } | Select-Object timestamp, operation_Id, reason | Format-List
    Write-Output 'Detalle guardado en dist/azure-telemetry.json (solo metadatos).'
} finally {
    Remove-Item -LiteralPath $bodyPath -Force
}
