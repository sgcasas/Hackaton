# Despliega servicio-ia en Azure Functions (Linux, Python 3.11, Functions v4) con Gemini por API key.
# No toca AWS: las URLs y el token van a Parameter Store a mano (/redtelecorp/gcp/url_principal,
# /redtelecorp/gcp/url_secundaria y /redtelecorp/gcp/token). Con una sola app, la misma URL va en las dos.
#
# Requisitos: Azure CLI con sesión (az login) y la key de Google AI Studio en $env:GEMINI_API_KEY.
# Uso:
#   $env:GEMINI_API_KEY = "<key>"
#   .\scripts\deploy-azure.ps1                       # genera un token y lo deja en el portapapeles
#   .\scripts\deploy-azure.ps1 -Token <token>        # reutiliza un token
param(
    [string]$Grupo = "redtelecorp-ia",
    [string[]]$Regiones = @("eastus", "eastus2", "westus2", "centralus", "brazilsouth"),
    [string]$Token = ""
)
# "Continue": con "Stop", PowerShell 5.1 corta el script por cualquier warning que az escriba en stderr.
# Los errores reales de az se detectan con $LASTEXITCODE.
$ErrorActionPreference = "Continue"
$env:PYTHONWARNINGS = "ignore"   # az de 32 bits avisa por cryptography en cada llamada
$fuente = Join-Path (Split-Path -Parent $PSScriptRoot) "azure\servicio-ia"

# az a veces no está en el PATH de una sesión abierta antes de instalarlo.
$az = (Get-Command az -ErrorAction SilentlyContinue).Source
if (-not $az) {
    $az = @("C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
            "C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin\az.cmd") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $az) { throw "No encuentro Azure CLI (az)." }
function Az { & $az @args; if ($LASTEXITCODE -ne 0) { throw "az $($args[0..2] -join ' ') falló" } }

$cuenta = & $az account show --query id -o tsv 2>$null
if (-not $cuenta) { Write-Host "No hay sesión de Azure. Corre 'az login' y vuelve a intentar." -ForegroundColor Red; exit 1 }

$key = $env:GEMINI_API_KEY
if (-not $key) { $key = [Environment]::GetEnvironmentVariable("GEMINI_API_KEY", "User") }
if (-not $key) { throw "Falta GEMINI_API_KEY en el entorno." }

$tokenNuevo = -not $Token
if ($tokenNuevo) { $Token = -join ((48..57) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ }) }

# Nombres globales únicos pero estables por suscripción, para poder volver a correr el script.
$sufijo = $cuenta.Replace("-", "").Substring(0, 6)
$app = "redtelecorp-ia-$sufijo"
$storage = "rtcia$sufijo"

Write-Host "Registrando proveedores (la primera vez tarda)..." -ForegroundColor Cyan
Az provider register --namespace Microsoft.Web --wait | Out-Null
Az provider register --namespace Microsoft.Storage --wait | Out-Null

$existe = & $az functionapp show -g $Grupo -n $app --query location -o tsv 2>$null
$plan = "existente"
if ($existe) {
    $region = $existe
} else {
    # Azure for Students limita regiones por política: se prueba cada región, primero Consumption y luego Flex Consumption.
    $region = $null
    foreach ($r in $Regiones) {
        Write-Host "`nProbando $r ..." -ForegroundColor Cyan
        & $az group create -n $Grupo -l $r -o none 2>$null
        & $az storage account create -n $storage -g $Grupo -l $r --sku Standard_LRS --allow-blob-public-access false -o none
        if ($LASTEXITCODE -ne 0) { continue }
        & $az functionapp create -n $app -g $Grupo -s $storage --consumption-plan-location $r `
            --os-type Linux --runtime python --runtime-version 3.11 --functions-version 4 -o none
        if ($LASTEXITCODE -eq 0) { $region = $r; $plan = "Consumption"; break }
        & $az functionapp create -n $app -g $Grupo -s $storage --flexconsumption-location $r `
            --runtime python --runtime-version 3.11 -o none
        if ($LASTEXITCODE -eq 0) { $region = $r; $plan = "Flex Consumption"; break }
        # El storage queda atado a la región: se borra para probar la siguiente.
        & $az storage account delete -n $storage -g $Grupo --yes -o none 2>$null
    }
    if (-not $region) { throw "Ninguna región/plan funcionó: $($Regiones -join ', ')" }
}
Write-Host "Function app: $app ($region, $plan)" -ForegroundColor Green

# Las settings con secretos no se imprimen (az devuelve todas las settings en la salida).
Az functionapp config appsettings set -g $Grupo -n $app -o none --settings `
    "TOKEN=$Token" "GEMINI_API_KEY=$key" "GEMINI_MODEL=gemini-3.5-flash-lite" "REGION_NAME=$region" | Out-Null

Write-Host "Publicando (zip + build remoto)..." -ForegroundColor Cyan
$zip = Join-Path ([IO.Path]::GetTempPath()) "servicio-ia-azure.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $fuente "function_app.py"), (Join-Path $fuente "host.json"), (Join-Path $fuente "requirements.txt") -DestinationPath $zip
Az functionapp deployment source config-zip -g $Grupo -n $app --src $zip --build-remote true --timeout 600 -o none
Remove-Item $zip

$url = "https://" + (Az functionapp show -g $Grupo -n $app --query defaultHostName -o tsv)
if ($tokenNuevo) {
    $copiado = $false
    foreach ($i in 1..3) { try { Set-Clipboard -Value $Token -ErrorAction Stop; $copiado = $true; break } catch { Start-Sleep 1 } }
    if ($copiado) { Write-Host "Token nuevo copiado al portapapeles (no se imprime)." }
    else { Write-Host "No pude copiar el token. Sácalo con: az functionapp config appsettings list -g $Grupo -n $app --query `"[?name=='TOKEN'].value`" -o tsv | Set-Clipboard" -ForegroundColor Yellow }
}
Write-Host "`nURL base: $url" -ForegroundColor Green
Write-Host "Prueba: $url/salud"
Write-Host "Parameter Store (lo carga quien tenga el Lab): url_principal = url_secundaria = $url ; token = el del portapapeles."
