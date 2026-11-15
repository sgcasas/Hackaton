# Runbook de failover: cambia la región activa en Parameter Store de las 2 regiones.
# La región activa es la única que corre el simulador, los schedulers y las Step Functions (parte B).
# Uso:  .\scripts\cambiar-region.ps1 -Activa us-west-2
param(
    [Parameter(Mandatory = $true)][ValidateSet("us-east-1", "us-west-2")][string]$Activa
)
foreach ($r in @("us-east-1", "us-west-2")) {
    aws ssm put-parameter --region $r --name /redtelecorp/region_activa --type String --value $Activa --overwrite | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "$r -> region_activa = $Activa" }
    else { Write-Host "$r no respondió (si es la región caída, es normal)" -ForegroundColor Yellow }
}
