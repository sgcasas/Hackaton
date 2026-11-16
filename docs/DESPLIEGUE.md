# Despliegue — Red TeleCorp

Pasos para levantar, probar y desplegar el sistema. El contrato está en [CONTRATO.md](CONTRATO.md).

## 0. Cómo levantar la base

```bash
# 1) Base en la región principal (crea las Global Tables)
sam deploy --template-file infra/template.yaml --stack-name redtelecorp-base \
  --region us-east-1 --parameter-overrides EsRegionPrincipal=true

# 2) Token del servicio de IA (SecureString: CloudFormation no lo puede crear)
aws ssm put-parameter --region us-east-1 --type SecureString \
  --name /redtelecorp/gcp/token --value '<token>'

# 3) Datos semilla
python3 scripts/semilla.py --cargar        # usa AWS_REGION=us-east-1

# 4) Validar contratos (antes de cada push que los toque)
pip install jsonschema && python3 scripts/validar_contratos.py
```

**Stacks:**
| Stack | Template | De quién |
|---|---|---|
| `redtelecorp-base` | `infra/template.yaml` | B |
| `redtelecorp-red` | `infra/lado-red.yaml` | B |
| `redtelecorp-usuario` | `infra/usuario.yaml` | A |
| `redtelecorp-frontend` | `infra/frontend.yaml` | A |

Orden: base → red → usuario → frontend. La SF tiene que existir antes de que A empiece a recibir reportes.

**Fase 3 (multi-región):**
- volver a desplegar us-east-1 con `RegionReplica=us-west-2`, para que las tablas se repliquen;
- desplegar el mismo template en us-west-2 con `EsRegionPrincipal=false`;

---

## 12. Lado red (B): desplegar y probar

**Requisitos:** AWS CLI, SAM CLI y Docker (para la imagen con scikit-learn). El stack base (§0) tiene que estar desplegado antes.

```bash
# Tests locales (AWS simulado con moto, no toca la cuenta)
pip install -r tests/requirements.txt && python3 -m pytest -q tests/red

# Learner Lab: no se pueden crear roles, se usa LabRole
ROL=$(aws iam get-role --role-name LabRole --query Role.Arn --output text)

# Build (necesita Docker) y deploy
sam build --template-file infra/lado-red.yaml
sam deploy --stack-name redtelecorp-red --region us-east-1 --resolve-s3 --resolve-image-repos \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --parameter-overrides RolArn=$ROL
#   (en una cuenta normal, sin RolArn: el stack crea sus propios roles)

# Primer modelo de ML (sin histórico entrena con tráfico sintético)
aws lambda invoke --function-name redtelecorp-entrenar --cli-binary-format raw-in-base64-out \
  --payload '{"dias":1}' /dev/stdout
```

**Escenarios de la demo** (el simulador los lee cada minuto):
```bash
esc() { aws ssm put-parameter --name /redtelecorp/simulador/escenario --type String --overwrite --value "$1"; }
esc '{"tipo":"normal"}'
esc '{"tipo":"caida_antena","objetivo":"A011"}'       # A011 < 60 GB/min  -> SF incidente (CAIDA)
esc '{"tipo":"corte_zona","objetivo":"Z03"}'          # Z03 en 0          -> INHABILITADA
esc '{"tipo":"saturacion_zona","objetivo":"Z02"}'     # > 129             -> SATURADA
esc '{"tipo":"antena_inestable","objetivo":"A007"}'   # sin cruzar umbrales: solo el ML -> ANOMALIA
# Para que la SF revise cada minuto en la demo (por defecto son 5):
aws ssm put-parameter --name /redtelecorp/sf/espera_seg --type String --overwrite --value 60
```

**Dónde mirar:**
- Step Functions → `redtelecorp-incidente`: el grafo de cada ejecución.
- DynamoDB → `redtelecorp-red`: `estadoActual` y `anomaliaScore` de cada antena.
- DynamoDB → `redtelecorp-incidentes`: los incidentes y su estado.
- S3 → `metricas/` e `incidentes/`.


---

## 13. Despliegue real (Learner Lab, 15-nov-2026)

| | URL |
|---|---|
| Portal (HTTPS, permite usar la ubicación) | https://redtelecorp-web-506630903590.s3.us-east-1.amazonaws.com/index.html |
| Portal (HTTP) | http://redtelecorp-web-506630903590.s3-website-us-east-1.amazonaws.com |
| API us-east-1 | https://0mbbbg8sf3.execute-api.us-east-1.amazonaws.com/prod |

Se despliega con `bash scripts/deploy-aws.sh correo1 correo2` desde una máquina con Docker (se usó la VM "MV Dev" del Lab).

**Lo que el Learner Lab no permite, y cómo quedó:**
| Bloqueado | Solución |
|---|---|
| Crear roles IAM | Todo usa `LabRole` (parámetro `RolArn`) |
| CloudFront (`CreateDistribution` y `CreateOriginAccessControl`) | Portal en S3. HTTPS por el endpoint REST del bucket |
| SES (`VerifyEmailIdentity`) | Avisos por correo por el tema SNS `redtelecorp-avisos`: un correo por incidente y estado, a los correos suscritos (cada uno confirma una vez). El SMS sigue por SNS |
| Step Functions `dynamodb:updateItem.waitForTaskToken` | No existe: la espera del operador usa `lambda:invoke.waitForTaskToken` |
| Réplicas de Global Tables hacia us-west-2 | Se quedan en `CREATING` o fallan con `UnrecognizedClientException`. **Hoy la solución corre en una sola región.** El diseño multi-región está en `ARQUITECTURA.md` y el script lo soporta con `REGIONES="us-east-1 us-west-2"` en una cuenta normal |

**Parámetro nuevo:** `/redtelecorp/notif/topic_arn` (lo crea `infra/lado-red.yaml`). `procesar-reporte` y la SF lo usan para los correos.
