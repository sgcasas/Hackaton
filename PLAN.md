# Plan de trabajo — Hackatón Red TeleCorp

**A = lado usuario** (portal, reportes, operador, IA en GCP) · **B = lado red** (Casas: antenas, ML, Step Functions, notificaciones)

Diseño completo en `ARQUITECTURA.md`. **Primero se cierran los contratos (`README.md`)**: después cada uno avanza sin bloquear al otro.

---

## 1. Contratos
Están en **`README.md`** (fuente única) + `contracts/` (JSON Schemas, ejemplos y semilla) + `infra/template.yaml` (base SAM).
Antes de cada push que toque un contrato: `python3 scripts/validar_contratos.py`.

---

## 2. TODO

> **Estado al 15-nov:** todo AWS desplegado en us-east-1 desde la VM "MV Dev" del Learner Lab (cuenta 506630903590).
> - **Portal (HTTPS):** https://redtelecorp-web-506630903590.s3.us-east-1.amazonaws.com/index.html. La versión HTTP no permite la ubicación del navegador.
> - **API:** https://0mbbbg8sf3.execute-api.us-east-1.amazonaws.com/prod
> - **Stacks:** `redtelecorp-base`, `redtelecorp-red`, `redtelecorp-usuario`, `redtelecorp-frontend`.

### Fase 0: juntos
- [x] Contratos: README, schemas, ejemplos y semilla.
- [x] El compañero revisó el README; sus notas ya están integradas.
- [x] Pruebas en Learner Lab. **Bloqueado:** CloudFront (CreateDistribution y CreateOriginAccessControl). **Funciona:** Global Tables (una región), Cognito, ECR/Lambda con imagen, Step Functions con LabRole y EventBridge Scheduler.
- [x] Base en us-east-1 + semilla cargada.

### Fase 1A: lado usuario (compañero)
- [x] API Gateway → SQS directo (probado: 202 y la incidencia queda guardada).
- [x] λ procesar-reporte (validación, zona y regla 10/24 h).
- [x] Cognito + λ api-operador.
- [x] Portal + panel. Queda en S3 por la falta de CloudFront; por HTTPS se usa la URL de arriba.
- [ ] **`servicio-ia` en GCP (compañero).** Después hay que poner `gcp/url_principal`, `gcp/url_secundaria` y `gcp/token` en el Parameter Store de **la cuenta 506630903590**. Hasta entonces, `tipoIA` y el resumen quedan vacíos.

### Fase 1B: lado red (Casas)
- [x] Simulador, procesar-métricas + ML, entrenar, SF incidente, SF diaria, Glue/Athena (22 tests).
- [x] Desplegado. Primer modelo entrenado (7200 filas sintéticas).
- [x] Arreglos al desplegar:
  - esperar al operador con `lambda:invoke.waitForTaskToken`, porque DynamoDB no lo soporta;
  - umbral de anomalía 0,75, porque con 0,6 hubo un falso positivo en A038.

### Fase 2: integración
- [x] Antena cae → SF abre el incidente (P2, `detectadoPor = SUPERVISION`) → espera → se cierra sola y queda en S3 (probado con A011).
- [ ] **SES:** verificar los correos del grupo (clic en el correo de AWS), poner `notif/remitente` y asignar esos correos a los clientes 1–30 (`scripts/correos_demo.py`).
- [ ] **Operador de Cognito:** crearlo. La contraseña la pone Casas.
- [ ] De punta a punta: 15 reportes de una zona (`scripts/pico.py --zona Z06 --n 15`) → incidente colectivo → correos → cierre.
- [ ] Antena inestable (escenario `antena_inestable`) → el ML la detecta antes que los usuarios.
- [ ] Pico de carga: `pico.py` con 190 reportes, sin pérdidas.
- [ ] Probar el panel del operador: login, incidentes y "Restablecido / Sin falla".
- [ ] Correr la SF diaria a mano una vez (Athena → `Agregados`) para que el panel tenga analítica.

### Fase 3: multi-región
- [ ] Desplegar en us-west-2 (base con `EsRegionPrincipal=false`, red, usuario) y agregar la réplica `RegionReplica=us-west-2` a la base de us-east-1.
- [ ] Poner las 2 APIs en `frontend/public/config.json` y volver a subir el frontend.
- [ ] Probar el failover: apagar la API de us-east-1 y ver que el portal pasa a us-west-2.

### Fase 4: entrega
- [ ] Actualizar `ARQUITECTURA.drawio`: el frontend es S3 sin CloudFront, y `incidente`/`analitica` son Lambdas.
- [ ] Exportar el diagrama a PNG.
- [ ] Guion del video (≤ 5 min, ARQUITECTURA §14), grabación y edición.
- [ ] Revisar el repo: **nada que parezca un informe**.
- [ ] Al terminar: desactivar el schedule del simulador (EventBridge Scheduler) para no gastar crédito del Lab.
- [ ] Subir a Canvas: video + repo + arquitectura.
