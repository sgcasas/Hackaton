# Plan de trabajo — Hackatón Red TeleCorp

**A = lado usuario** (portal, reportes, operador, IA en GCP) · **B = lado red** (Casas: antenas, ML, Step Functions, notificaciones)

Diseño completo en `ARQUITECTURA.md`. **Primero se cierran los contratos (`README.md`)**: después cada uno avanza sin bloquear al otro.

---

## 1. Contratos
Están en **`README.md`** (fuente única) + `contracts/` (JSON Schemas, ejemplos y semilla) + `infra/template.yaml` (base SAM).
Antes de cada push que toque un contrato: `python3 scripts/validar_contratos.py`.

---

## 2. TODO

### Fase 0: juntos (antes de separarse)
- [x] Escribir los contratos: README, schemas, ejemplos y semilla (B).
- [ ] **Compañero: revisar y aprobar el `README.md`.** Cualquier cambio se hace ahí, primero.
- [ ] Probar en la cuenta de AWS: Global Tables, la replicación de S3 a us-west-2, Cognito, SES, SMS por SNS y Lambda con imagen de contenedor (ECR). Anotar qué falla.
- [ ] Decidir la cuenta de GCP (o Azure como plan B).
- [x] Crear `infra/template.yaml` (SAM): tablas, bucket, colas + DLQ, parámetros y alarmas (validado con cfn-lint).
- [ ] Desplegar la base en us-east-1 y cargar la semilla (README §0).
- [ ] Crear el resto de carpetas a medida que se usen: `services/`, `stepfunctions/`, `gcp/`, `frontend/`.

### Fase 1A: lado usuario (compañero)
- [ ] API Gateway (REST) `POST /reportes` → SQS directo, con request validator (`contracts/schemas/reporte.sqs.schema.json`).
- [ ] λ procesar-reporte: validar en `Clientes`, buscar la antena más cercana para sacar la zona, `/clasificar`, guardar META + HIST, acuse, regla 10/24 h → iniciar SF (README §5).
- [ ] `servicio-ia` en Cloud Run + Gemini (us-east4 y us-central1).
- [ ] Cognito + λ api-operador (rutas de ARQUITECTURA §8).
- [ ] Portal del usuario + panel del operador, con failover a la segunda API.

### Fase 1B: lado red (Casas)
- [x] Datos semilla (`scripts/semilla.py`).
- [ ] λ simulador + EventBridge (1 min) → SQS `metricas`, con escenarios: normal, corte de zona, antena inestable.
- [ ] λ procesar-métricas: umbrales, estado de la antena en `Red`, `FALLA#`, escritura en S3 e inicio de la SF si cambia el estado.
- [ ] SF incidente: crear, vincular, notificar (deduplicado), verificar antenas, prioridad, `/resumir`, esperar restablecimiento, cerrar y guardar en S3.
- [ ] Notificaciones directas desde la SF (SES y SNS).
- [ ] ML: λ entrenar (contenedor, Isolation Forest con el histórico de S3) → `modelos/`. Inferencia dentro de procesar-métricas → evento `ANOMALIA`.
- [ ] SF diaria: recurrencia (Athena → `estructural`), analítica (→ `Agregados`) y reentrenamiento.
- [ ] Glue/Athena: tablas con partition projection sobre los prefijos de README §8.

### Fase 2: integración (juntos)
- [ ] De punta a punta: 15 reportes de una zona → incidente colectivo → notificaciones → cierre.
- [ ] De punta a punta: antena inestable → el ML la detecta antes que los usuarios (`detectadoPor = SUPERVISION`).
- [ ] Alarmas de CloudWatch en las DLQ.
- [ ] Script `scripts/pico.py`: carga de 500 reportes en 1 minuto, para confirmar que no se pierde ninguno.

### Fase 3: multi-región (juntos)
- [ ] Desplegar el mismo template en us-west-2, con `region_activa` en `us-east-1`.
- [ ] Probar el failover: apagar la API de us-east-1 y ver que el portal sigue funcionando.

### Fase 4: entrega
- [ ] Exportar `ARQUITECTURA.drawio` a PNG.
- [ ] Guion del video (≤ 5 min, ARQUITECTURA §14), grabación y edición.
- [ ] Revisar el repo: **nada que parezca un informe**.
- [ ] Subir a Canvas: video + repo + arquitectura.
