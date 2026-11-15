# Red TeleCorp — Hackatón Cloud Computing 2026-2

Sistema serverless (AWS + GCP) para detectar y gestionar incidencias de red cruzando los reportes de los usuarios con las métricas de las antenas.

| Documento | Para qué |
|---|---|
| `HACKATON-INFO.md` | Enunciado, rúbrica y notas |
| `ARQUITECTURA.md` / `.drawio` | Diseño completo |
| `PLAN.md` | TODO por fases y quién hace qué |
| **Este README** | **Contrato entre A (lado usuario) y B (lado red). Es la fuente única: si algo cambia, se cambia aquí primero** |

```
contracts/schemas/   JSON Schema de cada mensaje (fuente de verdad del formato)
contracts/ejemplos/  un ejemplo válido de cada schema
contracts/semilla/   zonas, antenas y clientes de prueba
infra/template.yaml  base SAM: tablas, bucket, colas, parámetros y alarmas
infra/lado-red.yaml  stack de B: Lambdas, Step Functions, Glue/Athena (ver §12)
services/red/        código de B (Python 3.12)
stepfunctions/       incidente.asl.json, diaria.asl.json
tests/red/           20 tests con AWS simulado (moto)
scripts/             validar_contratos.py, semilla.py
```

---

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
- repetir el paso 2 en us-west-2.

---

## 1. Nombres de recursos

Prefijo: `redtelecorp`. Todo se llama igual en las dos regiones.

| Recurso | Nombre |
|---|---|
| Tablas DynamoDB | `redtelecorp-incidencias`, `-red`, `-incidentes`, `-clientes`, `-notificaciones`, `-agregados` |
| Bucket S3 | `redtelecorp-<cuenta>-<region>` |
| Colas SQS | `redtelecorp-reportes` (+ `-dlq`), `redtelecorp-metricas` (+ `-dlq`) |
| Step Functions | `redtelecorp-incidente`, `redtelecorp-diaria` |
| Lambdas | `redtelecorp-procesar-reporte`, `-api-operador` (A) · `-simulador`, `-procesar-metricas`, `-entrenar`, `-incidente`, `-analitica` (B) |
| Glue / Athena | base de datos `redtelecorp` (tablas `metricas`, `reportes`, `incidentes`) · workgroup `redtelecorp` |
| Alarmas | `redtelecorp-dlq-reportes`, `redtelecorp-dlq-metricas` |

### Variables de entorno (todas las Lambdas)
| Variable | Valor |
|---|---|
| `PROYECTO` | `redtelecorp` |
| `TABLA_INCIDENCIAS` · `TABLA_RED` · `TABLA_INCIDENTES` · `TABLA_CLIENTES` · `TABLA_NOTIFICACIONES` · `TABLA_AGREGADOS` | nombres de la tabla anterior |
| `BUCKET` | `redtelecorp-<cuenta>-<region>` |
| `COLA_REPORTES_URL` · `COLA_METRICAS_URL` | outputs del stack base |
| `SF_INCIDENTE_ARN` | ARN de `redtelecorp-incidente` |
| `PARAM_PREFIX` | `/redtelecorp` |

**Regla de región activa:** toda función que **inicia** algo (schedulers, simulador, `StartExecution`) primero lee `/redtelecorp/region_activa`. Si no coincide con `AWS_REGION`, no hace nada. La ingesta (guardar reportes y métricas) sí corre en ambas regiones.

---

## 2. Enums y formatos comunes

| Campo | Valores |
|---|---|
| `estado` (incidencia) | `REGISTRADA`, `EN_REVISION`, `EN_ATENCION`, `SOLUCIONADA`, `CERRADA`, `RECHAZADO` |
| `estado` (incidente) | `ABIERTO`, `EN_ATENCION`, `ESPERANDO_OPERADOR`, `RESTABLECIDO`, `CERRADO` (abierto = cualquiera menos `CERRADO`) |
| `tipo` (problema) | `SIN_INTERNET`, `SIN_LLAMADAS`, `SIN_SMS`, `LENTITUD`, `SIN_SENAL`, `OTRO` |
| `tipoZona` | `RESIDENCIAL`, `COMERCIAL`, `EDUCATIVA`, `INDUSTRIAL`, `MEDICA`, `CRITICA` |
| `estadoAntena` | `NORMAL`, `CAIDA` (<60), `SATURADA` (>129), `INHABILITADA` (=0) |
| `evento` (SF) | `COLECTIVO`, `CAIDA`, `SATURADA`, `INHABILITADA`, `ANOMALIA` |
| `prioridad` | `P1` (crítica) … `P4` (baja). Como texto ordena bien en el GSI |
| `actor` (historial) | `SISTEMA`, `OPERADOR`, `SF` |
| `canales` | `email`, `sms` |

Otros formatos:
- **Fechas:** ISO 8601 en UTC, con `Z` (`2026-11-15T04:35:12Z`).
- **IDs:** zona `Z01`–`Z10` · antena `A001`–`A050` · `codigo` de reporte = `$context.requestId` de API Gateway (UUID).
- **DNI:** 8 dígitos · número: `9XXXXXXXX` · celular: `+519XXXXXXXX`.

---

## 3. DynamoDB

Todas las tablas usan llaves `pk` / `sk` (string) y son Global Tables con PITR, on-demand. Atributos en camelCase.

### `redtelecorp-incidencias` (escribe A; la SF de B actualiza el estado)
| pk | sk | Atributos |
|---|---|---|
| `INC#<codigo>` | `META` | `codigo, dni, numero, nombre, email, celular, canales[], lat, lon, antenaId, zonaId, fechaHoraUsuario, fechaRegistro, tipo, tipoIA, urgenciaIA, descripcion, estado, prioridad, incidenteId, motivoRechazo` |
| `INC#<codigo>` | `HIST#<fechaISO con microsegundos>` | `estadoAnterior, estadoNuevo, actor, comentario` (ej. `HIST#2026-11-15T04:35:12.123456Z`, para que ordene bien) |

| GSI | Llaves | Uso |
|---|---|---|
| `por-zona` | `zonaId` + `fechaRegistro` | Regla de 24 h (A) y vincular reportes (B) |
| `por-estado` | `estado` + `prioridad` | Panel del operador |
| `por-dni` | `dni` + `fechaRegistro` | "Mis reportes" del usuario |

- **`prioridad`** se guarda en `P4` al crear la incidencia, porque sin ese atributo no aparece en el GSI `por-estado`. La SF la reemplaza por la del incidente, también en las incidencias que se vinculan después.
- **`motivoRechazo`** solo va cuando `estado = RECHAZADO`: `CLIENTE_NO_VALIDO` o `TOPE_DE_REPORTES`.

**Ciclo que aplica la SF:** `REGISTRADA → EN_REVISION → EN_ATENCION → SOLUCIONADA → CERRADA`.

**A tiene que escribir en META** `zonaId`, `fechaRegistro`, `estado`, `dni`, `canales`, `email` y `celular`. Sin esos campos la SF no puede vincular ni notificar.
La **regla de 10** cuenta los `dni` distintos en `por-zona` con `fechaRegistro >= ahora − 24 h` y `estado != RECHAZADO`.

### `redtelecorp-red` (escribe B; A la lee para ubicar la zona)
| pk | sk | Atributos |
|---|---|---|
| `ZONA#<zonaId>` | `META` | `tipoEntidad=ZONA, zonaId, nombre, tipoZona, criticidad, lat, lon` |
| `ANTENA#<antenaId>` | `META` | `tipoEntidad=ANTENA, antenaId, zonaId, lat, lon, usuariosEstimados, estadoActual, ultimoGB, desde, anomaliaScore, estructural` |
| `ANTENA#<antenaId>` | `FALLA#<fechaISO>` | `estado, duracionMin, ttl` (90 días) |

GSI `por-tipo` = `tipoEntidad` + `pk`. Para la antena más cercana, A hace `Query tipoEntidad=ANTENA` (son 50) y calcula la distancia en memoria.

### `redtelecorp-incidentes` (escribe B)
| pk | sk | Atributos |
|---|---|---|
| `INCID#<nombreEjecucion>` | `META` | `id` (= nombre de la ejecución, ej. `zona-Z03-2026111504`), `origen, evento, zonaId, antenas[], reportes` (**número**), `usuarios` (número de DNI distintos), `afectados, prioridad, falla, estado, inicio, fin, duracionMin, detectadoPor (SUPERVISION\|USUARIOS), resumenIA, causaProbable, executionArn, taskToken, resolucion` |
| `ZONALOCK#<zonaId>` | `META` | `incidenteId`. Es un lock interno de B (un incidente abierto por zona): **A lo ignora** |

GSI `por-zona-estado` = `zonaId` + `estado`.

### `redtelecorp-clientes` (semilla de B; A la lee para validar)
pk `DNI#<dni>`, sk `NUM#<numero>`, con `nombre, email, celular`. Un reporte es válido solo si existe **ese** par DNI + número.

### `redtelecorp-notificaciones` (las dos partes; deduplicación)
pk `NOTIF#<referencia>#<estado>#<dni>`, con `canal, enviadoEn, ttl` (30 días). `referencia` es el `codigo` (en el acuse) o el `incidenteId`.

**Regla:** primero `PutItem` con `attribute_not_exists(pk)`. Solo si pasa, se envía. Si falla la condición, ya se había enviado.

### `redtelecorp-agregados` (escribe B; A la lee en el panel)
pk `AGG#<tipo>` (`ZONA`, `ANTENA`, `TIPO`, `DURACION`), sk `<YYYY-MM-DD>`, con `datos` (map `{clave: valor}`).

---

## 4. Mensajes SQS

| Cola | Escribe | Consume | Schema | Ejemplo |
|---|---|---|---|---|
| `redtelecorp-reportes` | API Gateway (A) | λ procesar-reporte (A) | `contracts/schemas/reporte.sqs.schema.json` | `contracts/ejemplos/reporte.sqs.json` |
| `redtelecorp-metricas` | λ simulador (B) | λ procesar-métricas (B) | `metrica.sqs.schema.json` | `metrica.sqs.json` |

- En `reporte.sqs`, `codigo` y `fechaRegistro` los agrega API Gateway como **atributos del mensaje** (`codigo` = `$context.requestId`, `fechaRegistro` = `$context.requestTimeEpoch` en ms). `procesar-reporte` los junta con el cuerpo y convierte la fecha a ISO con `Z`. El schema describe el reporte **ya combinado**.
- Las colas tienen `maxReceiveCount = 3` y, después, DLQ con alarma. Los consumidores deben ser **idempotentes**, usando `codigo` como llave.

---

## 5. Step Function `redtelecorp-incidente` (la implementa B; la dispara A o B)

**Input:** `contracts/schemas/sf-incidente.input.schema.json`
```json
{ "origen": "ZONA", "evento": "COLECTIVO", "zonaId": "Z03", "antenaId": null,
  "detectadoEn": "2026-11-15T04:35:12Z" }
```

| Quién | Cuándo | `name` de la ejecución |
|---|---|---|
| A (procesar-reporte) | La regla de 10/24 h se cumple | `zona-<zonaId>-<yyyyMMddHH>` (UTC, de `fechaRegistro`) |
| B (procesar-métricas) | La antena cambia de estado o hay anomalía | `antena-<antenaId>-<evento>-<yyyyMMddHHmm>` |

- Si `StartExecution` devuelve `ExecutionAlreadyExists`, se **ignora**: ya se está atendiendo.
- Si falla por otra razón (por ejemplo, la SF todavía no existe), A no reintenta ese mensaje: el siguiente reporte de la zona vuelve a disparar la regla. Por eso hay que desplegar el stack `redtelecorp-red` antes que `redtelecorp-usuario`.
- **A no tiene que revisar si hay un incidente abierto.** La SF lo hace: si la zona ya tiene uno, vincula los reportes nuevos y termina.

**Qué hace la SF con los datos de A:**
- Pone `estado` e `incidenteId` en las incidencias vinculadas.
- Escribe `HIST#` con `actor=SF`.
- Notifica (sección 7) y guarda el cierre en S3.

**Operador (`waitForTaskToken`):**
- La SF guarda `taskToken` en el incidente y pasa a `ESPERANDO_OPERADOR`.
- `api-operador` (A) llama a `SendTaskSuccess` con:
```json
{ "resolucion": "RESTABLECIDO", "comentario": "Se reinició la estación base" }
```
`resolucion` es `RESTABLECIDO` o `SIN_FALLA`.

---

## 6. Servicio de IA en GCP (lo hace A; lo usan A y la SF de B)

| Endpoint | Request | Response |
|---|---|---|
| `POST /clasificar` | `ia-clasificar.request.schema.json` | `ia-clasificar.response.schema.json` |
| `POST /resumir` | `ia-resumir.request.schema.json` | `ia-resumir.response.schema.json` |

- Header: `Authorization: Bearer <token>` (`/redtelecorp/gcp/token`).
- URLs: `/redtelecorp/gcp/url_principal` (us-east4) y `/redtelecorp/gcp/url_secundaria` (us-central1).
- La respuesta dice quién contestó en el header **`X-Fuente-IA`**: `gemini` o `reglas`. El cuerpo no lo incluye porque los schemas no admiten campos extra. Si Gemini falla, servicio-ia responde igual con reglas simples.
- **Timeout de 8 s.** Quien llama prueba la principal, luego la secundaria y, si ambas fallan, sigue **sin IA**: usa el `tipo` del usuario y deja el resumen vacío.

---

## 7. Notificaciones

| `estado` (en la llave NOTIF) | Quién envía | Asunto / SMS |
|---|---|---|
| `REGISTRADA` | A (acuse) | Recibimos tu reporte (código `<codigo>`). Te avisaremos cualquier novedad. |
| `EN_REVISION` | B (SF) | Tu inconveniente está siendo evaluado. |
| `EN_ATENCION` | B (SF) | Identificamos una incidencia en tu zona y ya la estamos atendiendo. |
| `SOLUCIONADA` | B (SF) | El servicio en tu zona fue restablecido. Gracias por tu paciencia. |

- **Correo:** SES, con remitente `/redtelecorp/notif/remitente`. **SMS:** SNS `Publish` al `celular`.
- El envío va solo por los `canales` que eligió el usuario.

---

## 8. S3 (`redtelecorp-<cuenta>-<region>`)

| Key | Escribe | Contenido |
|---|---|---|
| `metricas/dt=YYYY-MM-DD/hora=HH/<uuid>.jsonl` | B | Una línea por métrica: `{antenaId, ts, gbMin, estado, anomaliaScore}` |
| `reportes/dt=YYYY-MM-DD/<codigo>.json` | A | Reporte **sin DNI ni contacto**: `codigo`, `dniHash` (sha256), `zonaId`, `antenaId`, `tipo`, `tipoIA`, `fechaRegistro`, `estado` |
| `incidentes/dt=YYYY-MM-DD/<incidenteId>.json` | B | Incidente cerrado: `duracionMin`, `afectados`, `zonaId`, `antenas`, `detectadoPor`, `prioridad` |
| `modelos/isoforest/<version>.joblib` + `modelos/isoforest/latest.json` | B | Modelo de ML; `latest.json` = `{ "version": "...", "entrenadoEn": "..." }` |

Los prefijos `dt=` / `hora=` son para el partition projection de Athena (tablas en `infra/lado-red.yaml`).

**Regla para Athena:** cada objeto JSON va en **una sola línea**. Nada de `indent`: `json.dumps(x, separators=(",", ":"))`.

---

## 9. Parameter Store (`/redtelecorp/...`)

| Nombre | Tipo | Valor inicial | Lo usa |
|---|---|---|---|
| `umbrales/caida` · `umbrales/saturada` | String | `60` · `129` | B |
| `regla/usuarios` · `regla/horas` | String | `10` · `24` | A |
| `regla/tope_dni` | String | `5` (máximo de reportes por DNI en la ventana; lo crea el stack de A) | A |
| `prioridad/pesos` | String (JSON) | ver `infra/template.yaml` | B |
| `ml/umbral_anomalia` | String | `0.75` (con 0.6 hubo falsos positivos en horas pico) | B |
| `region_activa` | String | `us-east-1` | A y B |
| `gcp/url_principal` · `gcp/url_secundaria` | String | `pendiente` → URLs de Cloud Run | A y B |
| `gcp/token` | **SecureString** | por CLI (sección 0) | A y B |
| `notif/remitente` | String | correo verificado en SES | A y B |
| `simulador/escenario` | String (JSON) | `{"tipo":"normal"}` (ver §12) | B |
| `sf/espera_seg` · `sf/max_intentos` | String | `300` · `36` (cada cuánto se revisa la antena y cuántas veces antes de pasar al operador) | B |
| `ml/racha` | String | `3` (métricas anómalas seguidas para disparar `ANOMALIA`) | B |
| `recurrencia/min_fallas` | String | `3` (fallas en 7 días = problema estructural) | B |

---

## 10. Datos semilla (`contracts/semilla/`, los genera B)

| Archivo | Contenido |
|---|---|
| `red.json` | 10 zonas (Lima, al menos una de cada `tipoZona`) + 50 antenas (`A001`–`A050`, 5 por zona) |
| `clientes.json` | 200 clientes: DNI `70000001`… con número `987000001`… y correo `clienteN@ejemplo.com` |

Para la demo, cambien `email` / `celular` de algunos clientes por los del grupo (verificados en SES).

---

## 11. Quién entrega qué

| A entrega a B | B entrega a A |
|---|---|
| Incidencias con los campos de la sección 3 | Tablas, colas, bucket y parámetros (base desplegada) |
| `StartExecution` según la sección 5 | Semilla de `red` y `clientes` |
| `servicio-ia` desplegado y sus URLs en Parameter Store | `SF_INCIDENTE_ARN` (export `redtelecorp-sf-incidente-arn`) |
| `SendTaskSuccess` desde el panel | `Agregados` diarios para el panel |

**Cambiar un contrato:**
1. Editar este README y el schema correspondiente.
2. Correr `scripts/validar_contratos.py`.
3. Avisar al otro **antes** de hacer el push.

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
