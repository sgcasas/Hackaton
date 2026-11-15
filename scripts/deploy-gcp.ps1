# Despliega servicio-ia en GCP (us-east4 y us-central1) y guarda las URLs y el token en
# Parameter Store de AWS, en las 2 regiones (README §6 y §9):
#   /redtelecorp/gcp/url_principal, /redtelecorp/gcp/url_secundaria (String) y /redtelecorp/gcp/token (SecureString).
#
# Requisitos: gcloud instalado y con sesión (gcloud auth login), AWS CLI con credenciales del Lab.
# Uso:
#   .\scripts\deploy-gcp.ps1 -Proyecto mi-proyecto-gcp
param(
    [Parameter(Mandatory = $true)][string]$Proyecto,
    [string[]]$RegionesGcp = @("us-east4", "us-central1"),
    [string[]]$RegionesAws = @("us-east-1", "us-west-2"),
    [string]$Token = ""
)
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
$fuente = Join-Path $raiz "gcp\servicio-ia"

if (-not $Token) { $Token = -join ((48..57) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ }) }

gcloud config set project $Proyecto
Write-Host "Habilitando APIs (la primera vez tarda un poco)..." -ForegroundColor Cyan
gcloud services enable cloudfunctions.googleapis.com run.googleapis.com cloudbuild.googleapis.com `
    artifactregistry.googleapis.com aiplatform.googleapis.com

# La cuenta de servicio por defecto de Compute es la que usa la función: necesita llamar a Vertex AI.
$numero = gcloud projects describe $Proyecto --format "value(projectNumber)"
gcloud projects add-iam-policy-binding $Proyecto `
    --member "serviceAccount:$numero-compute@developer.gserviceaccount.com" `
    --role "roles/aiplatform.user" --condition=None | Out-Null

$urls = @()
foreach ($r in $RegionesGcp) {
    Write-Host "`nDesplegando servicio-ia en $r ..." -ForegroundColor Cyan
    gcloud functions deploy servicio-ia --gen2 --runtime python312 --region $r `
        --source $fuente --entry-point servicio_ia --trigger-http --allow-unauthenticated `
        --memory 512Mi --timeout 30s --min-instances 0 --max-instances 5 `
        --set-env-vars "TOKEN=$Token,PROYECTO=$Proyecto,GEMINI_LOCATION=$r,GEMINI_MODEL=gemini-2.5-flash"
    $url = gcloud functions describe servicio-ia --gen2 --region $r --format "value(serviceConfig.uri)"
    Write-Host "  URL: $url"
    $urls += $url
}

# README §6: principal = us-east4, secundaria = us-central1.
foreach ($ra in $RegionesAws) {
    Write-Host "`nGuardando URLs y token en Parameter Store ($ra)..." -ForegroundColor Cyan
    aws ssm put-parameter --region $ra --name "/redtelecorp/gcp/url_principal" --type String --value $urls[0] --overwrite | Out-Null
    aws ssm put-parameter --region $ra --name "/redtelecorp/gcp/url_secundaria" --type String --value $urls[-1] --overwrite | Out-Null
    aws ssm put-parameter --region $ra --name "/redtelecorp/gcp/token" --type SecureString --value $Token --overwrite | Out-Null
}

Write-Host "`nProbando /clasificar en cada región:" -ForegroundColor Green
foreach ($u in $urls) {
    $cuerpo = '{"descripcion":"no tengo señal desde la mañana en el hospital","tipoUsuario":"OTRO"}'
    try {
        $resp = Invoke-WebRequest -Method Post -Uri "$u/clasificar" -Headers @{ Authorization = "Bearer $Token" } `
            -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($cuerpo)) -UseBasicParsing
        Write-Host "  $u -> $($resp.Content)  (fuente: $($resp.Headers['X-Fuente-IA']))"
    } catch { Write-Host "  $u -> FALLA: $_" -ForegroundColor Red }
}
Write-Host "`nListo. Si la fuente sale 'reglas' en vez de 'gemini', revisa que Vertex AI esté habilitado y el permiso aiplatform.user."
