# Prueba rápida de lo que permite el AWS Academy Learner Lab.
# Uso (PowerShell, con las credenciales del Lab ya en ~/.aws/credentials):
#   .\scripts\probar-learner-lab.ps1
# Crea recursos chicos de prueba, imprime OK / FALLA y los borra al final.

$ErrorActionPreference = "Continue"
$R1 = "us-east-1"
$R2 = "us-west-2"
$sufijo = Get-Random -Maximum 99999
$cuenta = (aws sts get-caller-identity --query Account --output text)
if (-not $cuenta) { Write-Host "No hay credenciales de AWS. Copia las del Learner Lab a ~/.aws/credentials." -ForegroundColor Red; exit 1 }
$labRole = "arn:aws:iam::${cuenta}:role/LabRole"
$resultados = @()

function Probar($nombre, [scriptblock]$bloque) {
    Write-Host "`n== $nombre ==" -ForegroundColor Cyan
    $global:LASTEXITCODE = 0
    try { & $bloque } catch { $global:LASTEXITCODE = 1; Write-Host $_ }
    $ok = ($LASTEXITCODE -eq 0)
    $script:resultados += [pscustomobject]@{ Prueba = $nombre; Resultado = $(if ($ok) { "OK" } else { "FALLA" }) }
}

$tabla = "redtelecorp-prueba-$sufijo"
$b1 = "redtelecorp-prueba-$sufijo-east"
$b2 = "redtelecorp-prueba-$sufijo-west"

Probar "Lambda/SQS en us-west-2" {
    aws lambda list-functions --region $R2 --max-items 1 | Out-Null
    if ($LASTEXITCODE -eq 0) { aws sqs list-queues --region $R2 | Out-Null }
}

Probar "DynamoDB Global Table (replica en us-west-2)" {
    aws dynamodb create-table --region $R1 --table-name $tabla `
        --attribute-definitions AttributeName=pk,AttributeType=S `
        --key-schema AttributeName=pk,KeyType=HASH `
        --billing-mode PAY_PER_REQUEST `
        --stream-specification StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES | Out-Null
    if ($LASTEXITCODE -ne 0) { return }
    aws dynamodb wait table-exists --region $R1 --table-name $tabla
    aws dynamodb update-table --region $R1 --table-name $tabla --replica-updates "Create={RegionName=$R2}" | Out-Null
    if ($LASTEXITCODE -ne 0) { return }
    Write-Host "Esperando a que la réplica quede activa (puede tardar 1-3 min)..."
    for ($i = 0; $i -lt 24; $i++) {
        Start-Sleep -Seconds 10
        $estado = aws dynamodb describe-table --region $R1 --table-name $tabla --query "Table.Replicas[0].ReplicaStatus" --output text
        Write-Host "  réplica: $estado"
        if ($estado -eq "ACTIVE") { $global:LASTEXITCODE = 0; return }
    }
    $global:LASTEXITCODE = 1
}

Probar "DynamoDB PITR" {
    aws dynamodb update-continuous-backups --region $R1 --table-name $tabla `
        --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true | Out-Null
}

Probar "S3 replicación us-east-1 -> us-west-2 con LabRole" {
    aws s3api create-bucket --bucket $b1 --region $R1 | Out-Null
    aws s3api create-bucket --bucket $b2 --region $R2 --create-bucket-configuration LocationConstraint=$R2 | Out-Null
    aws s3api put-bucket-versioning --bucket $b1 --versioning-configuration Status=Enabled
    aws s3api put-bucket-versioning --bucket $b2 --versioning-configuration Status=Enabled
    $cfg = @{
        Role  = $labRole
        Rules = @(@{ ID = "todo"; Status = "Enabled"; Priority = 1; Filter = @{}; DeleteMarkerReplication = @{ Status = "Disabled" }; Destination = @{ Bucket = "arn:aws:s3:::$b2" } })
    } | ConvertTo-Json -Depth 6 -Compress
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value $cfg -Encoding ascii
    aws s3api put-bucket-replication --bucket $b1 --replication-configuration "file://$tmp" | Out-Null
    Remove-Item $tmp
}

Probar "Cognito User Pool" {
    $pool = aws cognito-idp create-user-pool --region $R1 --pool-name "redtelecorp-prueba-$sufijo" --query UserPool.Id --output text
    if ($LASTEXITCODE -eq 0) { aws cognito-idp delete-user-pool --region $R1 --user-pool-id $pool }
}

Probar "SES (lectura de cuota)" {
    aws ses get-send-quota --region $R1
}

Probar "SNS SMS (lectura de atributos)" {
    aws sns get-sms-attributes --region $R1 | Out-Null
}

Probar "ECR (para la Lambda con contenedor de Casas)" {
    aws ecr create-repository --region $R1 --repository-name "redtelecorp-prueba-$sufijo" | Out-Null
    if ($LASTEXITCODE -eq 0) { aws ecr delete-repository --region $R1 --repository-name "redtelecorp-prueba-$sufijo" --force | Out-Null }
}

Probar "CloudFront (lectura)" {
    aws cloudfront list-distributions --max-items 1 | Out-Null
}

Probar "Step Functions (lectura)" {
    aws stepfunctions list-state-machines --region $R1 --max-results 1 | Out-Null
}

Probar "Parameter Store SecureString" {
    aws ssm put-parameter --region $R1 --name "/redtelecorp-prueba-$sufijo/x" --type SecureString --value "hola" | Out-Null
    if ($LASTEXITCODE -eq 0) { aws ssm delete-parameter --region $R1 --name "/redtelecorp-prueba-$sufijo/x" }
}

# ---- Limpieza ----
Write-Host "`nLimpiando recursos de prueba..." -ForegroundColor Yellow
aws dynamodb update-table --region $R1 --table-name $tabla --replica-updates "Delete={RegionName=$R2}" 2>$null | Out-Null
Start-Sleep -Seconds 20
aws dynamodb delete-table --region $R1 --table-name $tabla 2>$null | Out-Null
foreach ($b in @($b1, $b2)) { aws s3 rb "s3://$b" --force 2>$null | Out-Null }
Write-Host "(Si la tabla no se borró porque la réplica seguía activa, bórrala a mano en la consola.)"

Write-Host "`n===== RESUMEN =====" -ForegroundColor Green
$resultados | Format-Table -AutoSize
