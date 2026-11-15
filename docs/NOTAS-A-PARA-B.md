# Notas de A sobre el contrato

La parte A sigue `README.md`. Abajo está lo poco que se aparta (hay que llevarlo al README si te parece bien) y lo que necesito que confirmes. No toqué `README.md`, `contracts/` ni `infra/template.yaml`: la parte A va en un stack aparte (`infra/usuario.yaml`).

## Cambios propuestos al README

1. **§4, `codigo` y `fechaRegistro`.** API Gateway no puede armar una fecha ISO: `$context.requestTimeEpoch` es un número en milisegundos. Por eso los dos van como atributos del mensaje SQS (`codigo` = requestId, `fechaRegistro` = epoch en ms), y `procesar-reporte` los junta y convierte a ISO con `Z` antes de guardar. Lo que queda guardado cumple `reporte.sqs.schema.json`. Propongo cambiar la frase de §4 a: "`codigo` y `fechaRegistro` los agrega API Gateway como atributos del mensaje; procesar-reporte los pasa al formato del schema".
2. **§3, `prioridad` inicial.** El GSI `por-estado` tiene `prioridad` como range key, y un ítem sin ese atributo no aparece en el índice. Por eso A guarda cada incidencia nueva con `prioridad = "P4"`. Tu SF la puede pisar cuando calcule la del incidente.
3. **§9, parámetro nuevo.** `/redtelecorp/regla/tope_dni` = `5`: máximo de reportes por DNI en la ventana, para que nadie llene la regla de 10 solo. Lo crea el stack de A.
4. **§3, atributos extra en META.** `nombre` (del cliente) y `motivoRechazo` (`CLIENTE_NO_VALIDO` o `TOPE_DE_REPORTES`) cuando el estado es `RECHAZADO`.
5. **§6, origen de la respuesta de IA.** Como los schemas de respuesta tienen `additionalProperties: false`, servicio-ia dice si contestó Gemini o las reglas en el header `X-Fuente-IA` (`gemini` o `reglas`), no en el cuerpo. Si Gemini falla, servicio-ia igual responde con reglas simples, así que "sin IA" solo pasa si las dos regiones de GCP están caídas.

## Preguntas

Tu commit "Lado red" ya respondió varias: la SF pone `prioridad` en las incidencias, `reportes` es un número, `id` es el nombre de la ejecución y tu stack es `redtelecorp-red`. A ya ignora los `ZONALOCK#`, escribe `HIST#` con microsegundos y guarda en S3 un JSON compacto en una línea. Quedan estas:

1. ¿Te sirven los 5 cambios de arriba? Si sí, ¿los pasas tú al README o los paso yo?
2. Si `StartExecution` falla porque tu SF todavía no está desplegada, A no reintenta ese mensaje: lo vuelve a intentar el siguiente reporte de la zona. ¿Te parece bien?
3. Un detalle de la semilla: Barranco (Z03) y Campus UTEC (Z05) están a 1,5 km, y las antenas se reparten hasta ±0,008° (unos 900 m). Un reporte a más de 500 m del centro de una de esas zonas cae en la otra el 2 % de las veces. Para la demo no importa si se reporta cerca del centro.

## Orden de deploy

`redtelecorp-base` (tu `infra/template.yaml`) → `redtelecorp-red` (tu `infra/lado-red.yaml`) → `redtelecorp-usuario` y `redtelecorp-frontend` (`scripts/deploy-aws.ps1`). Si A se despliega antes que tu stack, los reportes se guardan igual y la SF arranca con el primer reporte que llegue después de que exista.
