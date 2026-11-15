# Despliega en AWS: la base de Casas (si todavía no existe), el stack de la parte A y el frontend.
# Requisitos: AWS CLI, SAM CLI, Node 18+ y las credenciales del Learner Lab en ~/.aws/credentials.
#
# Uso (desde la raíz del repo):
#   .\scripts\deploy-aws.ps1                                  # Fase 1: solo us-east-1
#   .\scripts\deploy-aws.ps1 -Regiones us-east-1,us-west-2    # Fase 3: las 2 regiones (README §0)
#   .\scripts\deploy-aws.ps1 -SoloFrontend                    # recompila y sube solo el frontend
param(
    [string[]]$Regiones = @("us-east-1"),
    [switch]$SoloFrontend
)
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$principal = $Regiones[0]
$replica = $(if ($Regiones.Count -gt 1) { $Regiones[1] } else { "" })

function Salida($region, $stack, $clave) {
    aws cloudformation describe-stacks --region $region --stack-name $stack `
        --query "Stacks[0].Outputs[?OutputKey=='$clave'].OutputValue" --output text
}
function Existe($region, $stack) {
    aws cloudformation describe-stacks --region $region --stack-name $stack 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}
function Revisar($paso) { if ($LASTEXITCODE -ne 0) { throw "Falló: $paso" } }

if (-not $SoloFrontend) {
    # ---- 1. Base de Casas (infra/template.yaml). Si ya la desplegó él, no se toca, salvo para agregar la réplica.
    if (-not (Existe $principal "redtelecorp-base") -or $replica) {
        Write-Host "== base en $principal (réplica: '$replica') ==" -ForegroundColor Cyan
        $params = @("EsRegionPrincipal=true")
        if ($replica) { $params += "RegionReplica=$replica" }
        sam deploy --template-file infra/template.yaml --stack-name redtelecorp-base --region $principal `
            --no-confirm-changeset --no-fail-on-empty-changeset --parameter-overrides $params
        Revisar "base $principal"
    }
    if ($replica) {
        Write-Host "`n== base en $replica ==" -ForegroundColor Cyan
        sam deploy --template-file infra/template.yaml --stack-name redtelecorp-base --region $replica `
            --no-confirm-changeset --no-fail-on-empty-changeset --parameter-overrides EsRegionPrincipal=false
        Revisar "base $replica"
    }

    # ---- 2. Parte A (infra/usuario.yaml) en cada región.
    Write-Host "`n== sam build (parte A) ==" -ForegroundColor Cyan
    sam build -t infra/usuario.yaml
    Revisar "sam build"
    foreach ($r in $Regiones) {
        Write-Host "`n== parte A en $r ==" -ForegroundColor Cyan
        sam deploy --stack-name redtelecorp-usuario --region $r --resolve-s3 `
            --no-confirm-changeset --no-fail-on-empty-changeset
        Revisar "parte A $r"
    }
}

# ---- 3. config.json del frontend (orden = prioridad del failover).
$apis = @()
foreach ($r in $Regiones) {
    $apis += [ordered]@{
        region     = $r
        url        = (Salida $r "redtelecorp-usuario" "ApiUrl")
        userPoolId = (Salida $r "redtelecorp-usuario" "UserPoolId")
        clientId   = (Salida $r "redtelecorp-usuario" "UserPoolClientId")
    }
}
$config = ConvertTo-Json -InputObject @{ apis = @($apis) } -Depth 4
[IO.File]::WriteAllText((Join-Path $raiz "frontend/public/config.json"), $config, (New-Object System.Text.UTF8Encoding $false))
Write-Host "`nconfig.json:`n$config" -ForegroundColor Cyan

# ---- 4. Frontend (CloudFront + S3, solo en la región principal).
Push-Location frontend
if (-not (Test-Path node_modules)) { npm install --no-audit --no-fund; Revisar "npm install" }
npm run build
Revisar "npm run build"
Pop-Location

Write-Host "`n== frontend (CloudFront tarda unos minutos la primera vez) ==" -ForegroundColor Cyan
aws cloudformation deploy --region $principal --stack-name redtelecorp-frontend `
    --template-file infra/frontend.yaml --no-fail-on-empty-changeset
Revisar "frontend"
$bucket = Salida $principal "redtelecorp-frontend" "BucketWeb"
$distId = Salida $principal "redtelecorp-frontend" "DistribucionId"
$url = Salida $principal "redtelecorp-frontend" "Url"
aws s3 sync frontend/dist "s3://$bucket" --delete
Revisar "s3 sync"
aws cloudfront create-invalidation --distribution-id $distId --paths "/*" | Out-Null

Write-Host "`nListo." -ForegroundColor Green
Write-Host "Portal: $url"
foreach ($a in $apis) { Write-Host "API $($a.region): $($a.url)" }
