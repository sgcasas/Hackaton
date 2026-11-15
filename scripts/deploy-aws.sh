#!/bin/bash
# Despliegue completo en AWS Academy Learner Lab (probado el 15-nov-2026 desde una VM Ubuntu con Docker).
# Requisitos: AWS CLI, SAM CLI, Docker, Node 18+, Python 3 con boto3 y credenciales del Lab en ~/.aws/credentials.
# Uso:  bash scripts/deploy-aws.sh correo1@utec.edu.pe correo2@utec.edu.pe
#
# Limitaciones del Learner Lab que este script ya resuelve:
#  - No se pueden crear roles IAM: todo usa LabRole.
#  - CloudFront bloqueado: el portal queda en S3 (HTTPS por el endpoint REST del bucket).
#  - SES no deja verificar correos: los avisos van por el tema SNS redtelecorp-avisos (cada correo confirma una vez).
#  - Global Tables: las réplicas a us-west-2 se quedan en CREATING / fallan con UnrecognizedClientException.
#    Por eso el script despliega solo us-east-1. REGIONES="us-east-1 us-west-2" lo intenta en las dos (cuenta normal).
set -euo pipefail
cd "$(dirname "$0")/.."
C1=${1:-}; C2=${2:-}
REGIONES=${REGIONES:-us-east-1}; R1=${REGIONES%% *}
ROL=$(aws iam get-role --role-name LabRole --query Role.Arn --output text)
out(){ aws cloudformation describe-stacks --region $1 --stack-name $2 --query "Stacks[0].Outputs[?OutputKey=='$3'].OutputValue" --output text; }

echo "== builds"
sam build -t infra/lado-red.yaml --use-container --build-dir .aws-sam/red
sam build -t infra/usuario.yaml --use-container --build-dir .aws-sam/usuario

for R in $REGIONES; do
  echo "== $R: base + semilla"
  sam deploy --template-file infra/template.yaml --stack-name redtelecorp-base --region $R \
    --no-confirm-changeset --no-fail-on-empty-changeset --parameter-overrides EsRegionPrincipal=true
  AWS_REGION=$R AWS_DEFAULT_REGION=$R python3 scripts/semilla.py --cargar
  [ -n "$C1" ] && python3 scripts/correos_demo.py --correos "$C1${C2:+,$C2}" --region $R

  echo "== $R: lado red (B)"
  CORREOS=""; [ "$R" = "$R1" ] && CORREOS="CorreoAviso1=$C1 CorreoAviso2=$C2"
  sam deploy -t .aws-sam/red/template.yaml --stack-name redtelecorp-red --region $R --resolve-s3 --resolve-image-repos \
    --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --no-confirm-changeset --no-fail-on-empty-changeset \
    --parameter-overrides RolArn=$ROL $CORREOS
  aws lambda invoke --region $R --function-name redtelecorp-entrenar --cli-binary-format raw-in-base64-out \
    --payload '{"dias":1}' --cli-read-timeout 900 /tmp/entrenar-$R.json >/dev/null && cat /tmp/entrenar-$R.json; echo

  echo "== $R: lado usuario (A)"
  sam deploy -t .aws-sam/usuario/template.yaml --stack-name redtelecorp-usuario --region $R --resolve-s3 \
    --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --no-confirm-changeset --no-fail-on-empty-changeset
done

echo "== frontend (solo $R1), con las APIs en orden de failover"
printf '{"apis":[' > frontend/public/config.json; sep=''
for R in $REGIONES; do
  printf '%s{"region":"%s","url":"%s","userPoolId":"%s","clientId":"%s"}' "$sep" $R "$(out $R redtelecorp-usuario ApiUrl)" \
    "$(out $R redtelecorp-usuario UserPoolId)" "$(out $R redtelecorp-usuario UserPoolClientId)" >> frontend/public/config.json; sep=','
done
printf ']}\n' >> frontend/public/config.json
(cd frontend && npm ci --no-audit --no-fund && npm run build)
aws cloudformation deploy --region $R1 --stack-name redtelecorp-frontend --template-file infra/frontend.yaml --no-fail-on-empty-changeset
B=$(out $R1 redtelecorp-frontend BucketWeb)
aws s3 sync frontend/dist s3://$B --delete

echo
echo "Portal (HTTPS): https://$B.s3.$R1.amazonaws.com/index.html"
echo "Portal (HTTP):  $(out $R1 redtelecorp-frontend Url)"
for R in $REGIONES; do echo "API $R: $(out $R redtelecorp-usuario ApiUrl)"; done
