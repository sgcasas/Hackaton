# Hackatón Cloud Computing 2026-2 — Red TeleCorp

Fuente: https://hackutec-cloud-2026-2.vercel.app

---

## 1. Contexto

"Red TeleCorp" es una empresa de telecomunicaciones que da servicios de telefonía móvil, Internet y conectividad a millones de usuarios en distintas ciudades y zonas. Por la cantidad de clientes, dispositivos, estaciones base y demás infraestructura, mantener la continuidad y calidad del servicio es uno de sus principales desafíos operativos.

- La experiencia del usuario no depende solo de tener señal: una interrupción en una antena, una degradación, una saturación o una falla localizada puede afectar llamadas, mensajes, Internet y otros servicios.
- Un problema puede afectar a pocos usuarios o a cientos/miles en una misma zona.

### Fuentes de información actuales
1. Métricas generadas por las antenas.
2. Reportes de los usuarios.
3. Registros (logs) de los sistemas internos.

El volumen puede crecer mucho en ciertos periodos → se necesita procesar los eventos de forma oportuna.

---

## 2. Reglas de las antenas (simulación)

Cada antena genera periódicamente su tráfico procesado (GB por minuto).

| Estado | Condición |
|---|---|
| **Inhabilitada** | = 0 GB/min |
| **Caída** | < 60 GB/min |
| **Normal** | entre 60 y 129 GB/min |
| **Saturada** | > 129 GB/min |

> Nota: 0 también es < 60; se asume que **inhabilitada** tiene prioridad sobre **caída** cuando el valor es exactamente 0.

---

## 3. Problemas / requerimientos del cliente

### P1. Portal de gestión de incidencias para usuarios
El usuario registra un inconveniente con:
- Identificación
- Número asociado al servicio
- Ubicación aproximada
- Fecha y hora del inconveniente
- Tipo de problema
- Descripción

### P2. Consulta, administración, estados e historial
- Los reportes deben poder consultarse y administrarse después.
- Estados: **registrada → en revisión → siendo atendida → solucionada → cerrada**.
- Guardar el **historial** de cómo evolucionó cada incidente.

### P3. Detección de incidentes colectivos
- Si **10 o más usuarios diferentes** reportan un inconveniente relacionado con el servicio **en la misma zona** en **menos de 24 horas** → posible **incidente colectivo**.
- Al detectarlo: realizar una **verificación de la infraestructura** correspondiente.

### P4. Notificación a usuarios afectados
- Cuando se determine afectación en una zona, los usuarios que reportaron deben recibir comunicación por **correo electrónico y/o celular**.
- Mensajes según el estado del incidente:
  - El inconveniente está siendo evaluado.
  - Se identificó una incidencia en la zona.
  - El servicio fue restablecido.

### P5. Priorización
- No todos los incidentes pesan igual.
- Factores: **cantidad de afectados** (5 personas ≠ miles) y **tipo de zona** (residencial vs. industrial, institución educativa, centro médico, zona de servicios críticos).

### P6. Analítica / patrones
Saber:
- Qué **zonas** tienen más incidentes.
- Qué **antenas** fallan con más frecuencia.
- Qué **tipos de inconveniente** se reportan más.

Objetivo: identificar patrones y apoyar decisiones de operación.

### P7. Incidentes recurrentes
- Una antena puede fallar varios días y "recuperarse" cada vez.
- Si los incidentes se acumulan → posible **problema estructural** que requiere atención especializada.

### P8. Reducir el tiempo de detección / correlación
- Riesgo actual: los usuarios detectan el problema antes que la supervisión interna.
- Relacionar los **reportes de usuarios** con el **comportamiento de la infraestructura** (métricas de antenas).

### P9. Picos de carga / incidentes simultáneos
- Cortes de energía, eventos climáticos o fallas de infraestructura pueden generar muchos reportes en pocos minutos.
- El sistema debe soportarlo **sin pérdida de registros** ni degradación considerable del servicio.

### P10. Información histórica
Poder analizar:
- Evolución de los incidentes.
- Cuánto tiempo estuvieron activos.
- Qué zonas fueron afectadas.
- Cuántos usuarios estuvieron involucrados.
- Con qué frecuencia ciertos elementos de infraestructura fallaron.

### P11. Costo y escalabilidad
- Menor costo operativo posible.
- Escalar según usuarios y eventos.
- **No depender de infraestructura permanentemente encendida.**
- Solución eficiente, escalable y sostenible económicamente.

---

## 4. Rúbrica (20 pts)

| Criterio | Puntaje |
|---|---|
| Resolver la mayor cantidad de problemas del contexto dado (cada dolor del cliente convertido en solución real suma) | 0–3 |
| Persistencia y alta disponibilidad de los datos | 0–2 |
| Uso de Step Functions para automatización | 0–2 |
| Uso de 2 nubes o más | 0–3 |
| Arquitectura de la solución | 0–3 |
| Pitch de 5 minutos vendiendo y explicando rápidamente la idea | 0–2 |
| Solución completamente serverless | 0–2 |
| Uso de IA en su solución | 0–3 |
| **TOTAL** | **0–20** |

---

## 5. Notas (reglas de entrega)

- ⚠️ Si el video **sobrepasa los 5 minutos**, no se revisa el proyecto.
- Entregar vía **Canvas**: el **video**, el **repo de GitHub** y la **arquitectura de la solución**.
- ⚠️ Si colocan un **informe**, se **anula la participación**.
- ⚠️ Si no usan **al menos 2 nubes**, no se corrige (**Vercel no cuenta**).
- Usar algún **servicio de AWS para mantener segura la data de las variables globales** (secretos/config).
- "Usen su cabeza: esto es un curso de Cloud Computing, no de Ingeniería del Prompt."

---

## 6. Checklist rápido

- [ ] P1 Portal de registro de incidencias
- [ ] P2 Estados + historial
- [ ] P3 Regla ≥10 usuarios / misma zona / <24 h → verificación de infraestructura
- [ ] P4 Notificaciones (email / SMS) según estado
- [ ] P5 Priorización (afectados + tipo de zona)
- [ ] P6 Analítica (zonas, antenas, tipos)
- [ ] P7 Detección de recurrencia por antena
- [ ] P8 Correlación reportes ↔ métricas de antenas
- [ ] P9 Tolerancia a picos sin pérdida de registros
- [ ] P10 Histórico (duración, zonas, afectados, frecuencia)
- [ ] P11 Serverless, bajo costo, escalable
- [ ] Step Functions
- [ ] 2+ nubes (sin contar Vercel)
- [ ] IA en la solución
- [ ] Servicio de AWS para secretos/variables globales
- [ ] Persistencia y alta disponibilidad
- [ ] Diagrama de arquitectura
- [ ] Video ≤ 5 min (pitch)
- [ ] Repo de GitHub
- [ ] Sin informe
