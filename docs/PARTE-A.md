# Parte A: lado usuario

Portal y panel (React en CloudFront + S3), API Gateway directo a SQS, `procesar-reporte`, Cognito, `api-operador` y `servicio-ia` en GCP. Todo sigue el contrato de `README.md`. Lo que se aparta del contrato o falta acordar está en `docs/NOTAS-A-PARA-B.md`.

## Qué agrega la parte A

| Archivo | Contenido |
|---|---|
| `infra/usuario.yaml` | Stack `redtelecorp-usuario`, encima de la base (`infra/template.yaml`, stack `redtelecorp-base`): API, Cognito, las 2 Lambdas, el parámetro `regla/tope_dni` y una alarma. Usa las tablas, la cola y el bucket de la base por nombre. |
| `infra/frontend.yaml` | Stack `redtelecorp-frontend`: CloudFront + bucket privado del frontend (solo en us-east-1) |
| `services/usuario/` | `procesar_reporte.py`, `api_operador.py` y `comun.py` (Python 3.12, solo boto3) |
| `gcp/servicio-ia/` | Cloud Run function con `/clasificar`, `/resumir` y `/salud` (Gemini 2.5 Flash en su propia región) |
| `frontend/` | React + Vite: Reportar, Consultar y Operador |
| `scripts/` | `deploy-aws.ps1`, `deploy-gcp.ps1`, `preparar-demo.ps1`, `correos_demo.py`, `pico.py`, `probar-learner-lab.ps1`, `cambiar-region.ps1` |
| `tests/usuario/test_usuario.py` | Pruebas de las Lambdas con AWS simulado, las tablas de la base y la semilla de `contracts/` |

## Requisitos (Windows)

AWS CLI, SAM CLI, Node 18 o más, Python 3.11 o más con `boto3`, y `gcloud` para GCP. Las credenciales del Learner Lab van en `~/.aws/credentials` y vencen cada 4 horas.

## Orden para desplegar

Todos los comandos se corren en PowerShell desde la raíz del repo.

1. Probar qué deja el Lab (unos 5 minutos):

   ```powershell
   .\scripts\probar-learner-lab.ps1
   ```

2. Desplegar en us-east-1. Si la base de Casas todavía no existe, el script la crea con su template. Después despliega la parte A y el frontend. El stack de B (`redtelecorp-red`) se despliega aparte, como dice el README §12.

   ```powershell
   .\scripts\deploy-aws.ps1
   ```

3. Desplegar servicio-ia en GCP. El script guarda `gcp/url_principal`, `gcp/url_secundaria` y `gcp/token` en Parameter Store.

   ```powershell
   .\scripts\deploy-gcp.ps1 -Proyecto tu-proyecto-gcp
   ```

4. Preparar la demo. Verifica los correos en SES, configura el remitente, carga la semilla de Casas, pone los correos del grupo a los clientes 1 a 30 y crea el operador. Hay que hacer clic en el correo de verificación que manda AWS.

   ```powershell
   .\scripts\preparar-demo.ps1 -Correos "a@utec.edu.pe,b@utec.edu.pe" -Operador a@utec.edu.pe -Clave "Operador123"
   ```

5. Fase 3, multi-región. Agrega la réplica de las tablas, crea la base en us-west-2 y despliega la parte A allí. El portal queda con las 2 APIs.

   ```powershell
   .\scripts\deploy-aws.ps1 -Regiones us-east-1,us-west-2
   .\scripts\deploy-gcp.ps1 -Proyecto tu-proyecto-gcp      # para copiar los parámetros de GCP a us-west-2
   ```

## Probar

- Portal: la URL que imprime `deploy-aws.ps1`. Cliente de ejemplo: DNI `70000001`, número `987000001`.
- Incidente colectivo: `python scripts\pico.py --api <ApiUrl de us-east-1> --zona Z06 --n 15`. Usa los clientes 11 a 25.
- Pico de carga (P9): `python scripts\pico.py --api <ApiUrl> --zona Z01 --n 190 --desde 1 --hilos 30`. La API responde 202 al instante y la cola absorbe el resto. Ojo: el tope es de 5 reportes por DNI cada 24 h, y la semilla tiene 200 clientes.
- Failover: con el portal abierto, borra el stage `prod` de la API de us-east-1. El siguiente reporte entra por us-west-2 y la etiqueta de arriba a la derecha cambia de color.
- Pruebas locales: `pip install -r tests/requirements.txt` y luego `python -m pytest -q tests/usuario` (o `tests` para correr también las de B; pasan las 31).

## Qué está verificado y qué no

Verificado sin AWS real:

- Las 11 pruebas de las Lambdas, con las tablas y GSIs de la base y la semilla de `contracts/`. El mensaje leído de la cola cumple `reporte.sqs.schema.json` y el input de la SF cumple `sf-incidente.input.schema.json`.
- `infra/usuario.yaml` pasa `cfn-lint` y `sam validate`, y no crea roles IAM.
- El cuerpo que manda el portal cumple el schema del reporte.
- Las respuestas de `servicio-ia` cumplen los schemas de `/clasificar` y `/resumir`, también cuando Gemini no está disponible.
- Probé el frontend en un navegador contra una API simulada, incluido el paso a us-west-2.

Sin verificar: el deploy real en el Learner Lab. En particular, la integración API Gateway → SQS con `LabRole`, Global Tables con réplica, el login de Cognito desde el navegador, la llamada real a Gemini y CloudFront.

## Cosas a tener en cuenta

- SES en sandbox solo envía a correos verificados. Por eso `correos_demo.py` pone los del grupo en los clientes 1 a 30.
- El SMS por SNS probablemente no funcione en el Lab. La Lambda lo intenta y, si falla, sigue sin cortar el flujo.
- El token de servicio-ia queda como variable de entorno en GCP. Para algo más serio iría en Secret Manager.
- Para pasar la región activa a us-west-2: `.\scripts\cambiar-region.ps1 -Activa us-west-2`.
