# Deja lista la demo: verifica correos en SES, carga la semilla de Casas, pone los correos del grupo
# a los clientes 1..30 y crea el usuario operador en las 2 regiones.
# Uso:
#   .\scripts\preparar-demo.ps1 -Correos "a@utec.edu.pe,b@utec.edu.pe" -Operador a@utec.edu.pe -Clave "Operador123"
# (Cada correo recibe un mail de AWS: hay que darle clic al enlace para verificarlo.)
param(
    [Parameter(Mandatory = $true)][string]$Correos,
    [Parameter(Mandatory = $true)][string]$Operador,
    [Parameter(Mandatory = $true)][string]$Clave,
    [string[]]$Regiones = @("us-east-1", "us-west-2"),
    [string]$StackUsuario = "redtelecorp-usuario",
    [switch]$SinSemilla
)
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
$remitente = $Correos.Split(",")[0].Trim()

foreach ($r in $Regiones) {
    Write-Host "`n== $r ==" -ForegroundColor Cyan
    foreach ($c in $Correos.Split(",")) {
        aws ses verify-email-identity --region $r --email-address $c.Trim()
        Write-Host "  SES: verificación enviada a $($c.Trim())"
    }
    aws ssm put-parameter --region $r --name /redtelecorp/notif/remitente --type String --value $remitente --overwrite | Out-Null
    Write-Host "  Remitente: $remitente"

    $pool = aws cloudformation describe-stacks --region $r --stack-name $StackUsuario `
        --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text
    if ($pool -and $pool -ne "None") {
        aws cognito-idp admin-create-user --region $r --user-pool-id $pool --username $Operador `
            --user-attributes Name=email,Value=$Operador Name=email_verified,Value=true `
            --message-action SUPPRESS 2>$null | Out-Null
        aws cognito-idp admin-set-user-password --region $r --user-pool-id $pool --username $Operador `
            --password $Clave --permanent
        Write-Host "  Cognito: operador $Operador listo"
    } else {
        Write-Host "  Cognito: no encontré el stack $StackUsuario en $r" -ForegroundColor Yellow
    }
}

# La semilla va solo a la región principal: las Global Tables la replican.
if (-not $SinSemilla) {
    $env:AWS_REGION = $Regiones[0]; $env:AWS_DEFAULT_REGION = $Regiones[0]
    python (Join-Path $raiz "scripts\semilla.py") --cargar
    python (Join-Path $raiz "scripts\correos_demo.py") --correos $Correos --region $Regiones[0]
}
Write-Host "`nListo. Recuerda dar clic en los correos de verificación de SES." -ForegroundColor Green
