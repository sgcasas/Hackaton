# Arquitectura — Red TeleCorp (Hackatón Cloud Computing 2026-2)

> Versión 3: corregida y simplificada con el harness de `harness/` (31 checks, todos pasan). Requisitos en `HACKATON-INFO.md`.

---

## 0. Decisiones base

| Decisión | Elección |
|---|---|
| Nube principal | **AWS** — `us-east-1` (activa) + `us-west-2` (pasiva) |
| Segunda nube | **GCP** — `servicio-ia` en `us-east4` (principal, misma zona que us-east-1) + `us-central1` (secundaria) |
| Cómputo | Solo Lambda y Cloud Run functions (escalan a cero) |
| Datos operativos | DynamoDB **Global Table** on-demand con PITR |
| Histórico | **Un solo bucket S3** (métricas, reportes, incidentes, modelos) con replicación a us-west-2 + Athena |
| Orquestación | **2 Step Functions**: `SF incidente` y `SF diaria` |
| Ingesta | API Gateway **directo a SQS** (reportes) y SQS (métricas), ambas con DLQ |
| Secretos / config | **Parameter Store SecureString** (cifrado con KMS) en ambas regiones |
| ML | Isolation Forest para detectar anomalías por antena; entrena e infiere en Lambda con imagen de contenedor |
| IA (LLM) | Vertex AI Gemini, a través de `servicio-ia` |
| Failover de la API | En el cliente: el frontend conoce las 2 APIs y se pasa a us-west-2 si us-east-1 falla |
| IaC | AWS SAM (mismo template para las 2 regiones) |

**Servicios que se evitan:** EC2, RDS, ECS/EKS, Kinesis, NAT Gateway, OpenSearch, Lambdas en VPC, Route 53 (no hace falta un dominio) y Firehose.

---

## 1. Componentes (diseño final: 25 en el harness, 27 en la implementación; ver §11)

**Global**
- CloudFront + S3: portal del usuario y panel del operador.

**Por región AWS** (el mismo stack en `us-east-1` y `us-west-2`)
| Componente | Rol |
|---|---|
| API Gateway (REST) | `POST /reportes` → **SQS directo** (sin Lambda en medio). Rutas `/operador/*` → λ api-operador, protegidas con Cognito |
| Cognito | Login de los operadores |
| SQS reportes + DLQ | Buffer ante picos (P9) |
| λ procesar-reporte | Valida al cliente, ubica la zona, clasifica con IA, guarda, envía el acuse y cuenta la regla de 10 usuarios |
| λ api-operador | Consultas, cambio de estado, resolver incidentes, analítica |
| EventBridge Scheduler | Cada 1 min → simulador; diario → SF diaria. **Solo corre en la región activa** |
| λ simulador-antenas | Genera GB/min para unas 50 antenas en 10 zonas, con escenarios de falla |
| SQS métricas + DLQ | Buffer de métricas |
| λ procesar-métricas (contenedor) | Aplica umbrales y el **modelo de ML**, y actualiza el estado de la antena |
| SF incidente | Flujo de vida de un incidente (sección 3) |
| SF diaria | Recurrencia, analítica y reentrenamiento del ML |
| λ entrenar (contenedor) | Entrena el Isolation Forest con el histórico de S3 |
| λ incidente | Los pasos con lógica de SF incidente: abrir/unirse, vincular, notificar (deduplicado), verificar, prioridad, resumen IA, cerrar |
| λ analitica | Las consultas en Athena de SF diaria: recurrencia y agregados |
| DynamoDB Global Table | Incidencias, Red, Incidentes, Clientes, Notificaciones, Agregados |
| S3 (un bucket) | `metricas/`, `reportes/`, `incidentes/`, `modelos/` con replicación a la otra región |
| Athena | Consultas sobre el histórico |
| SES / SNS | Correo / SMS |
| Parameter Store | Credencial de GCP, umbrales, regla de 10 usuarios, pesos de prioridad, `region_activa` |
| CloudWatch | Alarmas en las DLQ y en los errores de envío |

**GCP** (`us-east4` y `us-central1`)
- Cloud Run function `servicio-ia` + Vertex AI Gemini.

---

## 2. Flujos

### 2.1 Reporte de usuario (P1, P2, P3, P9)
1. El portal hace `POST /reportes`. API Gateway valida el JSON con un request validator y lo **escribe directo en SQS**, respondiendo `202` con un código de seguimiento. No hay Lambda antes de la cola, así que un pico no puede causar un 429 por falta de concurrencia.
2. **λ procesar-reporte** (consume en lotes):
   1. **Anti-spoofing:** el DNI y el número deben existir juntos en la tabla `Clientes` (la base de clientes de la empresa). Si no, el reporte queda `RECHAZADO`. Además hay un tope de reportes por número.
   2. **Ubicación → zona:** con la lat/long del portal busca la **antena más cercana** en la tabla `Red`; la zona es la de esa antena.
   3. **IA:** llama a `servicio-ia` `/clasificar` en us-east4. Si falla, llama a us-central1, y si las dos fallan usa el tipo que eligió el usuario.
   4. Guarda la incidencia (`REGISTRADA`) y el primer evento de historial, y deja una copia en S3 `reportes/`.
   5. Manda el **acuse** ("recibimos tu reporte") por correo o SMS.
   6. **Regla colectiva:** cuenta los usuarios distintos de la zona en las últimas 24 h según la **hora de registro** (no la que escribe el usuario). Si son ≥ 10, inicia `SF incidente` con el **nombre de ejecución** `zona-<id>-<ventana>`. Step Functions no permite dos ejecuciones con el mismo nombre, así que nunca hay incidentes duplicados.
3. Si falla 3 veces, el mensaje va a la DLQ y salta una alarma de CloudWatch. **No se pierde.**

### 2.2 Métricas de antenas + ML (P7, P8, P9, P10)
1. EventBridge (cada 1 min) → λ simulador → SQS métricas → **λ procesar-métricas**.
2. Para cada métrica:
   - aplica los umbrales de Parameter Store (`0` inhabilitada, `<60` caída, `>129` saturada);
   - calcula el **score de anomalía** con el modelo de ML (cargado de S3 `modelos/` y guardado en caché mientras la Lambda siga activa). Así detecta tráfico raro **antes** de que cruce un umbral;
   - actualiza el estado actual de la antena en DynamoDB y escribe la métrica en S3 `metricas/`.
3. Solo si **cambia el estado** o hay una **anomalía**, inicia `SF incidente` con el nombre `antena-<id>-<evento>`. Así la supervisión puede detectar la falla **antes que los usuarios** (P8).

### 2.3 Estados e historial (P2)
- `REGISTRADA → EN_REVISION → EN_ATENCION → SOLUCIONADA → CERRADA` (más `RECHAZADO` si el cliente no se valida).
- Cada cambio escribe `HIST#<ts>` bajo la incidencia, con el estado anterior, el nuevo, el actor y un comentario.
- Los cambian `SF incidente` y el operador (vía λ api-operador).

---

## 3. Step Functions

### 3.1 SF incidente (un solo flujo, dos orígenes)
```
Inicio (origen = ZONA | ANTENA)
 → CrearIncidente (DynamoDB PutItem condicional)
 → VincularReportes de la zona (últimas 24 h) → EN_REVISION
 → Notificar("Tu inconveniente está siendo evaluado")
 → VerificarInfraestructura  ← correlación: estado y anomalías de las antenas de la zona
 → CalcularPrioridad
 → Choice
     ├─ Hay antena con falla o anomalía
     │    → ResumenIA (servicio-ia us-east4 → fallback us-central1)
     │    → EN_ATENCION → Notificar("Se identificó una incidencia en tu zona")
     │    → EsperarRestablecimiento (Wait 5 min + consultar antena, con tope)
     │    → SOLUCIONADA → Notificar("El servicio fue restablecido")
     └─ Sin falla visible
          → Operador revisa (waitForTaskToken desde el panel) → Notificar
 → Cerrar: duración, afectados y detectadoPor (SUPERVISION | USUARIOS) → S3 incidentes/
```
- **Notificar** lo hace λ incidente: primero un `PutItem` condicional sobre `NOTIF#incidente#estado#usuario` y, solo si pasa, envía por SES o SNS. Si el paso se reintenta, **no se manda dos veces**.
- **Esperar al operador** sí es directo: Step Functions guarda el `taskToken` en DynamoDB (`updateItem.waitForTaskToken`).
- Todos los pasos tienen `Retry` con backoff y `Catch`.

### 3.2 SF diaria (EventBridge, 1 vez al día)
```
Parallel
 ├─ Recurrencia: Athena (fallas por antena en 7 y 30 días) → marcar PROBLEMA_ESTRUCTURAL → ResumenIA del patrón (P7)
 ├─ Analítica:   Athena → tabla Agregados (zonas, antenas, tipos, duración, afectados) (P6, P10)
 └─ Reentrenar:  λ entrenar (contenedor) → nuevo modelo en S3 modelos/
```

---

## 4. Priorización (P5)
`prioridad = criticidad_zona × factor_afectados × factor_falla` → bandas **P1–P4**

| Tipo de zona | Criticidad | | Afectados | Factor | | Falla | Factor |
|---|---|---|---|---|---|---|---|
| Residencial | 1 | | < 10 | 1 | | Inhabilitada | 3 |
| Comercial | 1.5 | | 10–99 | 2 | | Caída | 2 |
| Educativa / Industrial | 2 | | 100–999 | 3 | | Saturada / anomalía | 1.5 |
| Médica / Servicios críticos | 3 | | ≥ 1000 | 4 | | Sin falla visible | 1 |

"Afectados" suma los reportantes y los usuarios estimados de la antena. Los pesos van en Parameter Store, así se ajustan sin redeploy.

## 5. Notificaciones (P4)
| Momento | Mensaje | A quién |
|---|---|---|
| Al registrar | Recibimos tu reporte (código) | El reportante (también si es un caso individual) |
| SF: inicio | Tu inconveniente está siendo evaluado | Reportantes vinculados |
| SF: falla confirmada | Se identificó una incidencia en tu zona | Reportantes vinculados |
| SF: restablecido | El servicio fue restablecido | Reportantes vinculados |

Canales: correo (SES) y/o SMS (SNS), según lo que dejó el usuario. Cada envío se deduplica y queda en el historial.

## 6. Servicio de IA (GCP) y ML
| | Dónde | Qué hace |
|---|---|---|
| **ML** | Lambda (contenedor) en AWS | Isolation Forest por antena y hora; detecta anomalías y se reentrena cada día |
| **LLM** `/clasificar` | Cloud Run + Gemini | Tipo de problema y urgencia a partir del texto libre |
| **LLM** `/resumir` | Cloud Run + Gemini | Resumen y causa probable de un incidente o de una antena recurrente |

**Failover de GCP:** lo decide **quien llama** (la λ o la SF): primero us-east4, después us-central1, y al final un fallback sin IA. El sistema nunca se cae por la IA.

---

## 7. Datos

**DynamoDB (Global Table, PITR)**
| Tabla | Llave | Contenido |
|---|---|---|
| Incidencias | `INC#id` / `META`, `HIST#ts` | Reporte, estado, zona, antena, prioridad, historial |
| Red | `ZONA#id`, `ANTENA#id` | Tipo de zona, coordenadas, estado actual, usuarios estimados, estructural |
| Incidentes | `INCID#id` | Origen, reportes vinculados, afectados, prioridad, inicio/fin, detectadoPor |
| Clientes | `DNI#…` / `NUM#…` | Base de clientes, para validar los reportes |
| Notificaciones | `NOTIF#incidente#estado#usuario` | Deduplicación de los envíos |
| Agregados | `AGG#tipo#fecha` | Analítica precalculada para el panel |

GSIs: `zona + fechaRegistro` (regla de 24 h), `estado + prioridad` (panel) y `usuario` (consulta del usuario).

**S3 (un bucket, replicado a us-west-2):** `metricas/`, `reportes/`, `incidentes/` (particionados por fecha y consultados con Athena) y `modelos/`.

## 8. API
| Método | Ruta | Quién | Destino |
|---|---|---|---|
| POST | `/reportes` | Usuario | **SQS** (integración directa) |
| GET | `/reportes/{codigo}` | Usuario | λ api-operador (solo lectura de su reporte) |
| GET | `/operador/incidencias` | Operador (Cognito) | λ api-operador |
| PATCH | `/operador/incidencias/{id}/estado` | Operador | λ api-operador |
| POST | `/operador/incidentes/{id}/resolver` | Operador | λ api-operador → SendTaskSuccess |
| GET | `/operador/analitica/{tipo}` | Operador | λ api-operador → Agregados |
| GET | `/operador/antenas` | Operador | λ api-operador |

---

## 9. Alta disponibilidad y multi-región

**Persistencia:** DynamoDB con PITR y Global Table, S3 con versionado y replicación, SQS con DLQ, idempotencia en las escrituras, nombres de ejecución únicos en las SF y notificaciones deduplicadas.

**Multi-región, activa-pasiva:**
- El **mismo template SAM** se despliega en `us-east-1` y `us-west-2`.
- Los datos se replican solos (Global Table y replicación de S3). Parameter Store se despliega en ambas regiones.
- **Solo la región activa procesa:** el flag `region_activa` de Parameter Store apaga los schedulers, el simulador y el inicio de las SF en la región pasiva. Así **no hay incidentes ni notificaciones duplicados**.
- **Failover de la API:** el frontend tiene las URLs de las 2 APIs. Si us-east-1 falla (timeout o 5xx), reintenta en us-west-2. Los reportes entran a la cola de la región pasiva y **no se pierden**.
- **Activar la región pasiva:** se cambia `region_activa` a `us-west-2` (un comando, descrito en el runbook del repo). Como los datos ya están replicados, retoma desde el mismo estado.
- **GCP:** `servicio-ia` en us-east4 y us-central1, con failover desde quien llama.
- Dentro de cada región, Lambda, SQS, DynamoDB, S3 y Step Functions ya son Multi-AZ por defecto.

## 10. Costo
- Todo es pago por uso y, con el volumen de la demo, entra casi entero en la capa gratuita.
- Las SF solo arrancan con un cambio de estado o anomalía de una antena, o al cruzar la regla de una zona. Nunca por cada métrica.
- En DynamoDB solo se guarda el estado actual; el histórico va a S3. No hay nada encendido 24/7.

---

## 11. Verificación con el harness (`harness/`)

`modelo.py` describe la arquitectura como grafo; `checks.py` tiene **31 checks** (P1–P11, rúbrica, notas, multi-región, ML y las 12 fallas que encontramos), y `bucle.py` prueba las simplificaciones.

**Sensibilidad:** el harness sí detecta los errores de la versión anterior (Lambda antes de la cola, respaldo de GCP mal dibujado, sin flag de región, sin validación de clientes, ML en un .zip y regiones de GCP lejanas). Las 6 pruebas fallan como deben.

**Bucle de simplificación** (35 → **25** componentes):
| # | Simplificación | Resultado |
|---|---|---|
| 1 | Unir SF recurrencia + analítica + reentrenar en **SF diaria** | ✅ aceptada |
| 2 | Unir SF falla de antena dentro de **SF incidente** | ✅ aceptada |
| 3 | Quitar Streams + λ evaluar-zona (la cuenta la hace procesar-reporte) | ✅ aceptada |
| 4 | Quitar λ notificar (SF llama directo a SES, SNS y DynamoDB) | ✅ aceptada (en la implementación vive dentro de λ incidente, ver abajo) |
| 5 | Secrets Manager → Parameter Store SecureString | ✅ aceptada |
| 6 | Route 53 → failover en el cliente | ✅ aceptada |
| 7 | Quitar Firehose (las Lambdas y las SF escriben directo a S3) | ✅ aceptada |
| 8 | Un solo bucket S3 (el modelo de ML en `modelos/`) | ✅ aceptada |
| 9 | Quitar la cola de métricas | ❌ falla P9 |
| 10 | Quitar Cognito | ❌ falla F12 |
| 11 | Quitar Athena | ❌ falla P6, P7, P10 |
| 12 | Una sola región AWS | ❌ falla multi-región |
| 13 | Una sola región GCP | ❌ falla multi-región y F2 |
| 14 | Quitar el ML | ❌ falla R-ML |
| 15 | Quitar las alarmas | ❌ falla F12 |
| 16 | Quitar la validación de clientes | ❌ falla F4 |
| 17 | Quitar SES | ❌ falla P4 |
| 18 | Quitar la Global Table | ❌ falla HA-PERS |

Desde la #9 en adelante todo falla: ese es el límite de la simplificación.

**Ajuste al implementar:** la lógica de las SF (vincular, verificar, prioridad, notificar deduplicado, recurrencia y agregados) quedó en **2 Lambdas** (`incidente` y `analitica`) en lugar de escribirse en ASL puro. En ASL era frágil y no se podía probar sin desplegar; así queda cubierta por 20 tests. Las SF siguen orquestando: esperas, decisiones, `waitForTaskToken`, reintentos y la ejecución en paralelo.

**Descartada por criterio** (el harness no la detecta): juntar SF incidente y SF diaria en una sola. Pasaría los checks, pero mezcla dos flujos sin relación y empeora la nota de "Arquitectura".

---

## 12. Mapeo con la rúbrica
| Criterio | Cómo se cumple |
|---|---|
| Problemas (3) | P1–P11 (secciones 2–6) |
| Persistencia y HA (2) | Global Table + PITR, replicación de S3, DLQ, idempotencia, activa-pasiva |
| Step Functions (2) | SF incidente + SF diaria |
| 2+ nubes (3) | AWS + GCP (Cloud Run + Vertex AI, en 2 regiones) |
| Arquitectura (3) | Basada en eventos, colas, 2 regiones por nube, verificada con el harness |
| Pitch (2) | Sección 14 |
| Serverless (2) | Solo Lambda, Cloud Run functions y servicios administrados |
| IA (3) | ML propio (anomalías) + LLM (clasificación y resúmenes) |
| Nota de AWS | Parameter Store SecureString (KMS) |

## 13. Estructura del repo
```
/infra          template.yaml (SAM, 2 regiones) + deploy de GCP
/services/red  simulador, procesar_metricas + entrenar (Dockerfile), incidente, analitica, modelo_ml (B)
/services/...  procesar-reporte, api-operador (A)
/stepfunctions  incidente.asl.json, diaria.asl.json
/gcp/servicio-ia
/frontend       portal + panel (con failover a la 2ª API)
/scripts        pico de reportes para la demo; runbook para cambiar region_activa
/harness        modelo.py, checks.py, bucle.py
/docs           ARQUITECTURA.drawio / .png
```

## 14. Demo (≤ 5 min)
1. (30 s) Problema y diagrama.
2. (45 s) Un usuario reporta; se ve el acuse y el estado.
3. (60 s) Escenario "antena inestable": el ML detecta la anomalía antes de cruzar el umbral y SF incidente arranca antes que los usuarios.
4. (60 s) El script manda 15 reportes de una zona: incidente colectivo, ejecución de la SF, correos que llegan y resumen de la IA.
5. (30 s) Restablecimiento → aviso → cierre del incidente.
6. (45 s) Se apaga la API de us-east-1: el portal sigue funcionando en us-west-2. Panel de analítica y costo.

## 15. Pendiente de decidir con el grupo
- **AWS Academy / Learner Lab:** no sé con certeza qué permite. Hay que probar antes Global Tables, la replicación de S3, Cognito, SES, SMS por SNS, Lambda con imagen de contenedor (ECR) y el uso de us-west-2. Sin roles IAM propios, hay que usar `LabRole`.
- **SES en sandbox:** verificar los correos del grupo para la demo.
- **Segundo proveedor:** GCP pide tarjeta. Si no se puede, la alternativa es Azure for Students (Azure Functions + Azure OpenAI en `eastus` y `centralus`).
- **Vertex AI:** confirmar que el modelo de Gemini elegido está disponible en us-east4.
- División del trabajo y fecha límite.
