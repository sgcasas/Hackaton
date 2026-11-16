# Red TeleCorp — Hackatón Cloud Computing 2026-2

Sistema serverless para detectar y gestionar incidencias de red. Cruza los reportes de los usuarios con las métricas de las antenas, abre incidentes (también los colectivos, desde 10 usuarios distintos en una zona en 24 h), avisa a los afectados y deja un panel para el operador.

## Nubes y servicios

- **AWS:** API Gateway, SQS, Lambda, DynamoDB (Global Tables), Step Functions, EventBridge, S3, Glue + Athena, SNS, SSM Parameter Store, Cognito, CloudWatch.
- **Azure:** Azure Functions (`servicio-ia`) con Gemini (Google AI Studio), que clasifica y resume reportes. Si Gemini no responde, usa reglas.

Diagrama: [ARQUITECTURA.drawio](ARQUITECTURA.drawio). Diseño: [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).

## Desplegar

Necesita AWS CLI, SAM CLI, Docker, Node 18+ y Python 3.12.

```bash
# Todo en AWS (Learner Lab, us-east-1): base, lado red, lado usuario y portal
bash scripts/deploy-aws.sh correo1@utec.edu.pe correo2@utec.edu.pe

# Servicio de IA en Azure; las URLs y el token se cargan en Parameter Store
.\scripts\deploy-azure.ps1 -Token <token>

# Tests (AWS simulado con moto) y validación de contratos
pip install -r tests/requirements.txt && python -m pytest -q tests
pip install jsonschema && python scripts/validar_contratos.py
```

Paso a paso (stacks, modelo de ML, escenarios de la demo): [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md).

## Despliegue real (Learner Lab, 15-nov-2026)

- Portal: https://redtelecorp-web-506630903590.s3.us-east-1.amazonaws.com/index.html
- API: https://0mbbbg8sf3.execute-api.us-east-1.amazonaws.com/prod

El Learner Lab no deja crear roles (todo usa `LabRole`), ni usar CloudFront (el portal va en S3), ni verificar correos en SES (los avisos van por un tema SNS). Las réplicas de Global Tables a us-west-2 fallan, así que hoy corre en una sola región; el diseño multi-región está documentado. Detalle en [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md).

## Estructura

```
frontend/      portal y panel del operador (React + Vite)
services/      Lambdas en Python: red/ (antenas, ML, incidentes) y usuario/ (reportes, API del operador)
stepfunctions/ incidente.asl.json y diaria.asl.json
azure/         servicio-ia (Azure Functions, desplegado)
gcp/           versión alternativa para GCP (no desplegada)
infra/         plantillas SAM/CloudFormation
contracts/     JSON Schemas, ejemplos y datos semilla
scripts/       despliegue, semilla, pico de carga, validación
tests/         pruebas de ambos lados
docs/          documentación
```

| Documento | Contenido |
|---|---|
| [docs/CONTRATO.md](docs/CONTRATO.md) | Contrato entre el lado usuario (A) y el lado red (B) |
| [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md) | Cómo levantar, probar y desplegar |
| [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) | Diseño completo |
| [docs/PLAN.md](docs/PLAN.md) | Estado y pendientes |
| [docs/PARTE-A.md](docs/PARTE-A.md) | Lado usuario |
| [docs/HACKATON-INFO.md](docs/HACKATON-INFO.md) | Enunciado y rúbrica |
