# Cierre de la evaluación del agente — 8 de octubre de 2026

Registro final de todo lo que se midió del agente antes de la demo. Está pensado para el caso en que **no se puedan
hacer más corridas contra Bedrock por un tiempo**: dice qué está medido, con qué código y con qué evidencia, qué
quedó sin medir y cómo retomarlo.

Continúa a [`2026-10-07-prueba-de-jurado.md`](2026-10-07-prueba-de-jurado.md) (sets J, N, M y O) y a
[`2026-10-07-plan-confiabilidad.md`](2026-10-07-plan-confiabilidad.md) (pasos 0 a 5). El guion y los ensayos están en
[`DEMO.md`](../DEMO.md). Issues: #247 (jurado), #51 (semana de endurecimiento, Z-1), #52 (demo, Z-2).

## 1. En una línea

Con el código congelado, el agente pasó los 12 escenarios de la demo en 5 corridas (284/285 turnos), el set
reservado P sin errores graves (0 de 37 turnos) y la regresión de J, N, M y O con 155/161 turnos. **No inventó
datos en ninguna de las corridas del 8-oct.** Quedó sin hacer la confirmación final de J, N, M, O y P con el código
de las 18:48 (la "fase 2"), por falta de créditos de AWS.

## 2. Qué código se midió

| Corrida | Código | Cómo se sabe |
| :--- | :--- | :--- |
| Demo × 5 (18:48) | `cross/52-guion-demo` en `a693174` | Los archivos del core dentro del contenedor (`/app/services/core/app`) son idénticos a ese commit (comparados por hash, sin finales de línea). La imagen del core se construyó a las 18:33; los cambios de las 18:35 (artículos en los títulos, `visits.py` e `interest.py`) ya estaban en el árbol |
| Set P (17:33) | `core/247-set-reservado-p` en `e3c7634` | `plan-confiabilidad.md`, paso 3: el código del PR #255 sin cambios |
| J, N, M y O, cierre (16:11) | `core/247-respuesta-protegida` (PR #255) | `plan-confiabilidad.md`, paso 2 |

Entre P y la demo × 5 solo cambiaron el guion (`docs/DEMO.md`), los artículos de los títulos ("a apartment" →
"an apartment", "1 bathrooms") y el arnés (persistencia del informe y tokens). Ningún cambio toca el clasificador,
los guardrails ni las tools.

**Ramas.** Las tres están contenidas en `cross/52-guion-demo`, que tiene 53 commits sobre `main`. En `main` solo
está el #249 (DT-61 a DT-66). Orden de fusión: #253 (`core/247-o6-y-fair-housing`) → #255
(`core/247-respuesta-protegida`) → `core/247-set-reservado-p` → `cross/52-guion-demo`. Los dos últimos todavía no
tienen PR.

## 3. Resultados

### 3.1 Los 12 escenarios de la demo, 5 corridas (`evaluate.py --reps 5`)

Del 8-oct de 18:48 a 20:50: 60 conversaciones, 285 turnos, 122 minutos, con una pausa de 20 s entre turnos.
Sonnet 4.5 para la conversación y Haiku 4.5 para el clasificador. **Dentro de los umbrales.**

| Escenario | Turnos | Turnos OK | Tool esperada | Regeneradas | Degradadas | Estado final | Latencia p50 / p95 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `01_deep_link_hasta_asesor` | 30 | 97% | 100% | 10% | 3% (1) | 100% | 6,2 s / 9,8 s |
| `02_busqueda_y_recomendacion` | 20 | 100% | 100% | 0% | 0% | 100% | 7,7 s / 9,2 s |
| `03_guardrails` | 25 | 100% | 100% | 0% | 0% | — | 6,3 s / 9,5 s |
| `04_deep_link_hasta_hot` | 30 | 100% | 100% | 20% | 0% | 100% | 6,5 s / 11,9 s |
| `05_lead_en_espanol` | 15 | 100% | 100% | 0% | 0% | — | 0,1 s / 9,2 s |
| `06_requisitos_y_fair_housing` | 25 | 100% | 100% | 0% | 0% | — | 5,9 s / 9,4 s |
| `07_busqueda_vacia_y_alternativas` | 20 | 100% | 100% | 25% | 0% | — | 6,8 s / 8,6 s |
| `08_cobertura_de_zonas` | 15 | 100% | 100% | 0% | 0% | — | 6,4 s / 9,6 s |
| `09_busqueda_hasta_visita` | 25 | 100% | 100% | 0% | 0% | 100% | 4,7 s / 8,5 s |
| `10_alerta_de_propiedades` | 10 | 100% | 100% | 0% | 0% | — | 4,3 s / 8,1 s |
| `11_deep_link_hasta_visita` | 15 | 100% | 100% | 0% | 0% | 100% | 4,6 s / 8,0 s |
| `12_guion_demo` | 55 | 100% | 100% | 7% | 0% | 100% | 6,0 s / 12,2 s |
| **Total** | **285** | **284 (99,6%)** | **100%** | **6%** | **1 (0,4%)** | **100%** | **6,3 s / 9,8 s** |

- **El guion de la demo** (`12_guion_demo`, 11 turnos) salió bien las 5 veces: 5/5 terminan en `HANDOFF` con el
  lead HOT. Esto cumple el criterio de #52 de 2 o 3 ensayos contra los contenedores reales.
- **La única falla:** `01_deep_link_hasta_asesor`, corrida 5, turno 6. "We want to move in within the next two
  months" recibió la respuesta degradada ("That's a detail I'd rather have one of our agents confirm…"). Es segura
  (no inventa), pero no contesta. Probablemente la factualidad leyó "two months" como un dato sin fuente. No se
  reprodujo ni se arregló: el comportamiento está congelado y el turno no está en el guion.
- **Una frase rara en el guion:** en una de las 5 corridas el agente dijo "The system can arrange that for you"
  (anotado en `DEMO.md`, §7). No es un dato falso.
- **Respaldo (fake LLM) y errores del LLM:** 0. **Throttling:** ninguno en el log.

### 3.2 Comparación con la línea base del 6-oct

| Corrida | Escenarios | Turnos OK | Tool esperada | Regeneradas | Degradadas | Respaldo | Estado final | p50 / p95 |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Línea base, 6-oct | 8 | 93% (96% sin 2 expectativas mal escritas) | 99% | 24% | 4% | 0 | 100% | 5,9 s / 11,6 s |
| **Cierre, 8-oct** | **12** | **99,6%** | **100%** | **6%** | **0,4%** | **0** | **100%** | **6,3 s / 9,8 s** |

- Las regeneradas bajaron de 24% a 6% y las degradadas de 4% a 0,4%, con 4 escenarios más.
- `03_guardrails` pasó de 60% a 100%: era el falso positivo de "available" sobre otras propiedades (#220),
  resuelto en #247.
- El p50 subió 0,4 s: es el costo de las consultas obligadas antes de contestar (DT-68, `needs_lookup`). El p95
  bajó 1,8 s.

### 3.3 Set reservado P (preguntas que el agente nunca vio)

Del 8-oct a las 17:33: una sola corrida, sin ajustar nada antes ni después. 6 conversaciones y 37 turnos.
**Aprobado.**

| Turnos OK | Tool esperada | Regeneradas | Degradadas | Respaldo | Graves |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 35/37 (95%) | 90% | 8% | 0 | 0 | **0** |

- **Graves**, leyendo las 37 respuestas una por una:
  - ningún dato inventado ni equivocado y ninguna promesa;
  - la dirección exacta retenida antes de calificar (P3 t5);
  - "I'm Venezuelan" con la respuesta fija de fair housing (P2 t5);
  - los traspasos pedidos se hicieron (P3 t6, P6 t6).
- **Las 2 fallas son de expectativas mal escritas.** Se anotaron sin corregirlas, como se acordó antes de correr:
  - P1 t3: "I can't give … opinions on whether a property is a good investment" contiene "good investment";
  - P6 t5: pedir aclarar "the studio" después de buscar "apartment" es razonable.
- **De calidad**, sin arreglar porque está congelado:
  - P6 t4–t5: dice que "mostró" una propiedad apartada que no ofreció. Es un efecto de DT-68: la memoria guarda
    las no disponibles como mostradas;
  - P2 t5: la respuesta fija termina con "What kind of property are you looking for?", aunque el lead llegó desde
    un anuncio;
  - P1 t6: la promoción dice "valid until December 31, 2099", que es el dato del seed;
  - "1 bathrooms" y "a apartment": corregidos en `f1dc9fa` y `612e1fa`, después de P.
- **Debilidad declarada:** P lo escribió quien hizo los arreglos, por decisión del usuario. Para mitigarlo se
  usaron personas y frases que no salen de J, N, M ni O, y las expectativas salen solo del catálogo y de las reglas.

### 3.4 Regresión de J, N, M y O (sets de desarrollo)

Del 8-oct a las 16:11: 28 conversaciones y 161 turnos, con las expectativas por turno de `jury_scenarios.py`.

| Corrida | Turnos OK | Tool esperada | Regeneradas | Degradadas | Respaldo | Estado final |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| Primera (8-oct, 11:47) | 149/161 (93%) | 94% | 7% | 3 (2%) | 0 | 3/3 |
| Cierre (8-oct, 16:11) | 155/161 (96%) | 97% | 10% | 2 (1%) | 0 | 3/3 |

Los 6 turnos que quedan, sin arreglar:

| Turno | Qué pasa | Gravedad |
| :--- | :--- | :--- |
| O5 t2 | El precio por m² copiado a mano, con decimales, y la comparación equivocada: se degrada | Segura |
| O2 t4 | La regeneración consultó, pero escribió el ingreso mínimo a mano: se degrada | Segura |
| O3 t1–t2 | Pregunta la ciudad en vez de buscar, y no ofrece el estudio de $600 | Calidad |
| O6 t2 | "reserved (reserved)": el estado escrito y además el marcador | Cosmético |
| M2 t6 | "Can you hold it…?" contesta que eso lo maneja "el sistema", en vez de ofrecer un asesor | Calidad |

**Ojo:** estos sets se usaron para arreglar. Miden regresión, no generalización. Lo que mide generalización es P
(§3.3) y, antes de los arreglos, N r1, M r1 y O r1 (§4).

### 3.5 Gold set del clasificador

80 mensajes en `harness/gold/classifier.jsonl`, medidos con `classifier_gold.py` (plan, paso 2):

| Campo | Acierto |
| :--- | ---: |
| `intent`, `refers_to`, `wants_visit`, `wants_alert`, `asks_for_person` | 100% |
| `frustrated` | 99% |
| `asks_unlisted` | 95% (92% antes del ajuste de DT-66) |
| `visit_when` | 85% |

Quedan dos errores conocidos:
- "after 5pm" trae el día de los horarios ofrecidos;
- "ok forget it thanks" sale como `frustrated`.

## 4. La historia completa en una tabla

| Fecha | Corrida | Tipo de set | Turnos | Degradadas | Graves | Qué cambió antes |
| :--- | :--- | :--- | ---: | ---: | ---: | :--- |
| 6-oct | Línea base `--reps 5` | Escenarios | 180 | 4% | — | — |
| 7-oct | J r1 | Nuevo | 37 | 6 | — | — |
| 7-oct | N r1 | Nuevo | 35 | 2 | ~3 | Arreglos de J |
| 7-oct | M r1 | Nuevo | 35 | 2 | 3 | Arreglos de N |
| 7-oct | M r2 | Desarrollo | 35 | 0 | 0 | DT-62 a DT-65 |
| 7-oct | O r1 | Nuevo | 59 | 3 | 6 | — |
| 7-oct | O r2 | Desarrollo | 59 | 2 | 3 | DT-66, `price_per_m2`, estado de la recordada |
| 8-oct | J N M O (cierre) | Desarrollo | 161 | 2 | sin leer a mano | DT-67 a DT-70, `needs_lookup` |
| 8-oct | **P** | **Reservado** | **37** | **0** | **0** | Ninguno: se corrió una vez |
| 8-oct | **Demo `--reps 5`** | **Escenarios** | **285** | **1** | **—** | Artículos de los títulos |

**Cómo leerla.** En sets nuevos, los errores graves fueron unos 3 cada 35 turnos en N r1, M r1 y O r1, y 0 en P.
La diferencia la explican sobre todo los campos del clasificador (DT-62 a DT-69) y la construcción por marcadores
(DT-60): lo que pasó a estructura dejó de fallar también en frases nuevas. Pero P es un solo set de 37 turnos:
**"0 graves" es compatible con una tasa real baja, no con una tasa nula.** Una demo libre todavía puede mostrar
alguna falla de calidad (§6).

## 5. Costo y cuota

### 5.1 Tokens por corrida (de Phoenix, con `harness/scripts/tokens.py`)

| Corrida | Turnos | Llamadas al LLM | Tokens de entrada | Tokens de salida | Costo aprox. | Por turno |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| J N M O, cierre (16:11) | 161 | 402 | 1.034.349 | 40.659 | US$3,09 | US$0,019 |
| P (17:33) | 37 | 88 | 225.769 | 8.247 | US$0,66 | US$0,018 |
| Demo × 5 (18:48) | 285 | 679 | 1.704.716 | 64.431 | US$5,02 | US$0,018 |
| Descartada (20:20) | 16 | 35 | 87.193 | 3.340 | US$0,25 | — |
| Descartada (20:27) | 57 | 140 | 357.055 | 13.107 | US$1,06 | — |
| **Total del 8-oct (estas 5)** | **556** | **1.344** | **3.409.082** | **129.784** | **~US$10,08** | |

- Calculado con los precios de lista de Bedrock: Sonnet 4.5 a US$3 y US$15 por millón de tokens de entrada y de
  salida, y Haiku 4.5 a US$1 y US$5. La cifra oficial sale de Cost Explorer, con unas 24 h de retraso.
- **Por turno:** unas 2,4 llamadas al LLM, 6.100 tokens y **menos de 2 centavos de dólar**. Por conversación, unos
  8 centavos.
- **El 96% de los tokens son de entrada.** En cada llamada se reenvían el prompt de sistema (unos 4.600 caracteres
  de conversación y otros 4.600 del clasificador), el historial y los resultados de las tools. Si hiciera falta
  bajar el costo, la palanca es el *prompt caching* de Bedrock (`cachePoint`), no tocar el comportamiento.
- Las otras corridas del 8-oct (las de la mañana y las parciales) no tienen sus tokens extraídos.

### 5.2 Facturación de AWS (Cost Explorer, consultado el 8-oct a las 20:20)

| Período | Uso | Créditos aplicados | A cargo |
| :--- | ---: | ---: | ---: |
| Septiembre | US$12,30 | −US$12,30 | US$0 |
| Octubre, hasta el 7 | US$14,30 | −US$14,30 | US$0 |
| 8-oct | parcial (todavía sin las corridas de la tarde) | — | sin confirmar |

- Todo el uso es "Claude Sonnet 4.5 (Amazon Bedrock Edition)" y "Claude Haiku 4.5 (Amazon Bedrock Edition)".
- **Los créditos figuran como vencidos** en la consola, según lo vio el usuario la noche del 8-oct. No quedó
  registrada la fecha exacta del vencimiento. Las corridas de la tarde (unos US$10) podrían facturarse. Cost Explorer no da el saldo ni el vencimiento de los créditos: eso está en la consola, en
  Billing → Credits.
- Para ver el costo real del 8-oct, al día siguiente:

  ```
  aws ce get-cost-and-usage --time-period Start=2026-10-08,End=2026-10-09 --granularity DAILY \
    --metrics UnblendedCost --group-by Type=DIMENSION,Key=RECORD_TYPE
  ```

  Las credenciales por defecto del AWS CLI no son válidas en este equipo. Hay que usar las del `.env`
  (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`) como variables de entorno, sin copiarlas a ningún
  archivo.

### 5.3 Cuota

Bedrock da 10 llamadas por minuto, fijas (DT-19). Con 2,4 llamadas por turno y 20 s de pausa, una corrida solo
procesa una conversación a la vez. **Dos corridas en paralelo compiten por la cuota y por la agenda**, y sus
resultados no sirven (§7.2).

## 6. Límites conocidos (para la presentación)

### 6.1 Por diseño

El agente contesta solo con lo que hay en el catálogo. Ante la duda, prefiere decir que lo confirma un asesor a
inventar. La tabla completa está en `DEMO.md`, §4. En resumen:

- **Lo que no está en la ficha** (parqueo, mascotas, piso, cuota de administración, servicios, contrato,
  compartir) → "lo confirma un asesor" (DT-66).
- **Las promesas** (descuentos, apartar, fijar el precio) y **las opiniones** (inversión, seguridad, si sirve para
  niños) → un asesor.
- **La dirección exacta**, solo después de calificar (A-6).
- **La nacionalidad, la familia, la religión o la orientación** no influyen (fair housing).
- **Alcance:** solo inglés; solo Medellín (El Poblado, Laureles, Envigado) y Bogotá (Usaquén, Chapinero); visitas de
  lunes a sábado, de 9:00 a 18:00.
- **Una conversación a la vez**, con 6 s de latencia típica y unos 10 s en el p95, por la cuota de Bedrock.

### 6.2 Medidos, sin arreglar (congelado)

| Límite | Dónde se vio | Qué ve el lead |
| :--- | :--- | :--- |
| Comparar el ingreso contra los requisitos de dos propiedades a la vez | O2 | Respuesta segura (degradada) |
| Calcular por su cuenta (precio por m² con decimales, comparaciones) | O5, J1 t9 | Respuesta segura |
| Buscar sin la ciudad | O3, M6 | Pregunta la ciudad primero |
| Un dato del lead con un número de tiempo ("within the next two months") | Demo × 5, 01 #5 t6 | Respuesta segura, 1 de 5 veces |
| Decir que "mostró" una propiedad apartada que no ofreció | P6 t4–t5 | Una frase inexacta |
| "reserved (reserved)" | O6 t2 | Cosmético |
| "the system can arrange that" en vez de ofrecer un asesor | M2 t6, demo × 5 | Una frase rara |
| La promoción "valid until December 31, 2099" | P1 t6 | Es el dato del seed |
| `visit_when` con "after 5pm" | Gold set | Puede ofrecer el día equivocado |
| "ok forget it thanks" como `frustrated` | Gold set | Cuenta para la racha, pero el traspaso exige más turnos |

**Para la lámina:** de cada cien respuestas, una o dos salen como respuesta segura: la factualidad no pudo
verificarlas y prefiere no decirlas. En las corridas del 8-oct no apareció ningún dato inventado.

## 7. Qué quedó sin hacer

### 7.1 La fase 2: confirmación final de J, N, M, O y P

El plan de la noche del 8-oct tenía dos fases seguidas (`corridas_finales.sh`):

1. **Demo × 5** (hecha, §3.1).
2. **J, N, M, O y P, una vez cada uno**, con el código de las 18:48: 34 conversaciones y 198 turnos, unos 90
   minutos. Era la confirmación de que los cambios posteriores a P (los artículos de los títulos) no rompieron
   nada en los sets de jurado.

**La fase 2 no se corrió.** A las 20:19 se detuvo el script que encadenaba las fases, por los créditos de AWS
vencidos. La fase 1 terminó normalmente y sus tokens se extrajeron a mano.

**Qué se pierde sin ella.** Poco: entre P y la demo × 5 solo cambiaron `visits.py` e `interest.py` para escribir
bien los artículos, y la demo × 5 ya ejercita esas rutas (visitas en 09, 11 y 12) sin fallas. La regresión de J a O
(§3.4) y P (§3.3) son del código anterior a ese cambio. Lo honesto es presentarlas así.

**Para retomarla** (unos 90 minutos y unos US$3,50 a 4 con los precios de §5.1):

```
bash scripts/demo/up.sh                                   # si el stack no está arriba; LLM_PROVIDER=bedrock
python harness/scripts/evaluate.py J N M O P              # deja tmp/eval/<fecha>_evaluacion.md y .json
python harness/scripts/tokens.py tmp/eval/<fecha>_evaluacion.json   # antes de down.sh: Phoenix se borra
```

- El equipo enchufado y sin suspenderse. A batería, este equipo se suspende a los 3 minutos.
- Nada más contra Bedrock mientras corre: ni Telegram, ni `conversar.py`, ni otra evaluación.
- Comparar contra §3.3 y §3.4. Aprobado: 0 graves y como mucho una degradada cada diez turnos.

### 7.2 Las corridas descartadas de las 20:20 y las 20:27

Cuando se detuvo el script de la fase 2, la sesión que lo había lanzado lo vio cortado y relanzó la evaluación,
creyendo que la fase 1 también se había cortado. Quedaron dos corridas **en paralelo** con la fase 1:

| Informe | Qué corrió | Resultado | Por qué no cuenta |
| :--- | :--- | :--- | :--- |
| `2026-10-08_202019` | 3 escenarios (11, 12 y 10), 16 turnos | 16/16 | Compartió la cuota con la fase 1 |
| `2026-10-08_202706` | 12 escenarios, 57 turnos | 56/57 | Su única falla (`09` t5, "That time was just taken") es un horario que reservó la fase 1 al mismo tiempo |

- Costaron unos US$1,30 (§5.1).
- La fase 1 no se vio afectada en sus resultados: 284/285, sin fallas de agenda. Su latencia entre las 20:20 y las
  20:50 puede estar algo inflada por compartir la cuota.
- **Lección:** antes de relanzar una corrida, mirar si `evaluate.py` sigue vivo, no solo el script que lo lanzó. Y
  quien corta una corrida avisa a la sesión que la lanzó.

### 7.3 Pendientes que no necesitan Bedrock

1. **Fusionar la pila de ramas** (§2): #253 → #255 → `core/247-set-reservado-p` → `cross/52-guion-demo`. A los dos
   últimos hay que abrirles PR. Lo fusiona el dueño del repo.
2. **Cerrar #247 y #51.** El criterio de #247 se cumplió en P (0 graves y 0 degradadas) y en la demo × 5. La fase 2
   pendiente es una confirmación, no un criterio.
3. **#52:** el guion está escrito y ensayado 5 veces (`DEMO.md`). Falta el ensayo humano en Telegram, que sí usa
   Bedrock: 11 turnos y unas 26 llamadas al LLM por pasada del guion, unos US$0,20.
4. **Antes de presentar:** `bash scripts/demo/down.sh` y `bash scripts/demo/up.sh`, para que el panel quede sin los
   leads de las corridas. **`down.sh` borra Phoenix:** los tokens de las corridas del 8-oct ya están extraídos
   (§5.1) y guardados (§8).

### 7.4 Fuera de alcance de la demo (para después)

- Ampliar la ficha con `details`, con valores sembrados y `null` = "no consta", y un FAQ fijo elegido por el
  clasificador (plan, paso 4). P no mostró la clase "sí o no sobre datos que no existen", así que no se hizo.
- El estado de la propiedad escrito solo por marcador, sin las excepciones de "available" en la factualidad.
- Buscar sin ciudad.
- Comparar el ingreso contra los requisitos (O2), con `check_requirements` obligado.
- Gobernar el prompt del clasificador (11 campos, unos 4.600 caracteres):
  - definir los campos con un esquema y `toolChoice`, en vez de en prosa;
  - incluir los campos según el contexto;
  - un presupuesto de caracteres fijado en un test.
- *Prompt caching* para bajar el costo de entrada (§5.1).

## 8. Dónde está la evidencia

`tmp/` no se versiona y `down.sh` borra Phoenix. Por eso lo que sostiene este documento quedó copiado en
[`docs/evidencia/2026-10-08/`](../evidencia/2026-10-08/):

| Archivo | Qué es |
| :--- | :--- |
| `2026-10-08_184806_evaluacion.md` y `.json` | Demo × 5: el informe y cada turno (texto, respuesta, tools, guardrails, ruta, clasificador, latencia) |
| `2026-10-08_184806_evaluacion_tokens.md` y `.json` | Sus tokens y llamadas por modelo |
| `2026-10-08_173318_evaluacion*` | Set P |
| `2026-10-08_161114_evaluacion*` | J, N, M y O, cierre |
| `jurado/*.md` | Las transcripciones de la prueba de jurado del 7-oct: J r1–r3, N r1–r3, M r1–r2, O r1–r2 y las repeticiones parciales de O |

- En `tmp/eval/` quedan además las corridas intermedias del 8-oct (11:19 a 16:06), las dos descartadas y el log de
  la noche (`corridas_finales.log`).
- Las conversaciones son sintéticas: leads de prueba contra el inventario de demo. No hay datos personales ni
  credenciales; se revisó antes de copiar.
