# Evidencia de la evaluación del 7 y 8 de octubre de 2026

Son los informes y transcripciones que respaldan
[`2026-10-08-cierre-de-evaluacion.md`](../../presentations/2026-10-08-cierre-de-evaluacion.md). Están copiados de
`tmp/eval/` y `tmp/jury/`, que no se versionan. Además, `scripts/demo/down.sh` borra Phoenix, de donde salen los
tokens.

Las conversaciones son sintéticas: leads de prueba contra el inventario de demo, sin datos personales ni
credenciales. Cada `.json` trae, turno por turno, el texto, la respuesta, las tools, los guardrails que saltaron,
la ruta, la salida del clasificador y la latencia.

## Evaluación en vivo (`harness/scripts/evaluate.py`)

| Corrida | Informe | Tokens | Datos por turno |
| :--- | :--- | :--- | :--- |
| Los 12 escenarios de la demo × 5 (8-oct, 18:48; cierre, §3.1) | [informe](2026-10-08_184806_evaluacion.md) | [tokens](2026-10-08_184806_evaluacion_tokens.md) | `2026-10-08_184806_evaluacion.json` y `_tokens.json` |
| Set reservado P (8-oct, 17:33; cierre, §3.3) | [informe](2026-10-08_173318_evaluacion.md) | [tokens](2026-10-08_173318_evaluacion_tokens.md) | `2026-10-08_173318_evaluacion.json` y `_tokens.json` |
| J, N, M y O, regresión de cierre (8-oct, 16:11; cierre, §3.4) | [informe](2026-10-08_161114_evaluacion.md) | [tokens](2026-10-08_161114_evaluacion_tokens.md) | `2026-10-08_161114_evaluacion.json` y `_tokens.json` |

## Prueba de jurado (`harness/scripts/jury.py`, 7-oct)

Son las transcripciones que se leyeron a mano. Los resultados de cada corrida están en
[`2026-10-07-prueba-de-jurado.md`](../../../archive/presentations/2026-10-07-prueba-de-jurado.md).

| Set | Corridas |
| :--- | :--- |
| J | [r1](jurado/2026-10-07_J_r1.md) · [r2](jurado/2026-10-07_J_r2.md) · [r3](jurado/2026-10-07_J_r3.md) |
| N | [r1](jurado/2026-10-07_N_r1.md) · [r2](jurado/2026-10-07_N_r2.md) · [r3](jurado/2026-10-07_N_r3.md) (solo N5 y N6) |
| M | [r1](jurado/2026-10-07_M_r1.md) · [r2](jurado/2026-10-07_M_r2.md) |
| O | [r1](jurado/2026-10-07_O_r1.md) · [r2](jurado/2026-10-07_O_r2.md) |
| O, repeticiones parciales | [22:39](jurado/2026-10-07_223953_O.md): O6 y O9, ya con `turn.decision` · [22:48](jurado/2026-10-07_224811_O.md): O9 con `TRACE_CONTENT=1` · [23:05](jurado/2026-10-07_230546_O.md): O6 y O9, después del paso 1 (DT-67) |
| O, copias con marca de hora | [19:21](jurado/2026-10-07_192128_O.md) es idéntica a O r1, y [21:40](jurado/2026-10-07_214026_O.md) a O r2 |
