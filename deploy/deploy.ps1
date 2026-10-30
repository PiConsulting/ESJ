param(
    [ValidateSet('validate', 'provision', 'publish')]
    [string]$Action = 'validate',
    [string]$ResourceGroup = 'rg-pi-epre-dev',
    [string]$Subscription = 'd468911c-54a0-4a0b-af8c-938d81aeea30'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$deploymentName = 'epre-dev-infrastructure'
$templatePath = Join-Path $PSScriptRoot 'azuredeploy.json'

if ($Action -eq 'publish') {
    $appName = az deployment group show --subscription $Subscription --resource-group $ResourceGroup --name $deploymentName --query properties.outputs.functionName.value --output tsv
    if ($LASTEXITCODE -ne 0 -or -not $appName) { throw 'No se pudo obtener la Function creada por esta plantilla.' }
    Push-Location $projectRoot
    try {
        & .\.venv\Scripts\python.exe -m scripts.package_function
        if ($LASTEXITCODE -ne 0) { throw 'Fallo el empaquetado.' }
        az functionapp deployment source config-zip --subscription $Subscription --resource-group $ResourceGroup --name $appName --src dist/esj-function.zip --build-remote true --output none
        if ($LASTEXITCODE -ne 0) { throw 'Fallo la publicacion; revisar el estado antes de repetir.' }
    } finally { Pop-Location }
    return
}

$settingsPath = Join-Path $projectRoot 'local.settings.json'
$endpoint = (Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json).Values.FABRIC_GRAPHQL_ENDPOINT
if (-not $endpoint -or $endpoint -notmatch '^https://[^/]+\.graphql\.fabric\.microsoft\.com/.+/graphql$') {
    throw 'Falta un endpoint Fabric valido en local.settings.json.'
}
$distPath = Join-Path $projectRoot 'dist'
New-Item -ItemType Directory -Path $distPath -Force | Out-Null
$parametersPath = Join-Path $distPath 'azure.parameters.json'
@{fabricEndpoint = @{value = $endpoint}} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $parametersPath -Encoding UTF8
try {
    az deployment group validate --subscription $Subscription --resource-group $ResourceGroup --name $deploymentName --template-file $templatePath --parameters "@$parametersPath" --output none
    if ($LASTEXITCODE -ne 0) { throw 'La validacion de infraestructura fallo; no se aprovisiono.' }
    if ($Action -eq 'provision') {
        az deployment group create --subscription $Subscription --resource-group $ResourceGroup --name $deploymentName --mode Incremental --template-file $templatePath --parameters "@$parametersPath" --query properties.outputs --output json
        if ($LASTEXITCODE -ne 0) { throw 'Fallo el aprovisionamiento; revisar recursos parciales antes de repetir.' }
    }
} finally {
    Remove-Item -LiteralPath $parametersPath -Force
}
