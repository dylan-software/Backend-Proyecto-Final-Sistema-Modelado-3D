# Sistema de Modelado 3D — Backend

Servidor web asíncrono que recibe el escaneo 3D de una habitación desde el frontend, detecta sus
superficies (suelo, paredes y techo), pide a un modelo de IA a través de OmniRoute una propuesta de
mobiliario, descarta los muebles que no caben o chocan con algo y guarda el resultado en PostgreSQL.

> El servidor Express anterior sigue en `src/` y es el que construyen `Dockerfile` y `render.yaml`.
> Su guía de despliegue está en [docs/despliegue-express.md](docs/despliegue-express.md).

## Pila tecnológica

| Componente | Uso |
|---|---|
| **FastAPI** | Microframework web asíncrono (ASGI). Valida cada petición con Pydantic y publica la documentación OpenAPI en `/docs`. |
| **Uvicorn** | Servidor ASGI que ejecuta la aplicación. |
| **SQLAlchemy 2 (async) + asyncpg** | Acceso a PostgreSQL sin bloquear el bucle de eventos. |
| **NumPy / SciPy** | Álgebra vectorial y `cKDTree` para búsquedas de proximidad. |
| **scikit-learn** | `RANSACRegressor` para ajustar planos robustos. |
| **OpenCV** | Decodificar la foto opcional de la habitación y extraer su paleta con k-means. |
| **httpx** | Cliente HTTP asíncrono hacia OmniRoute. |
| **pytest + aiosqlite** | Pruebas automatizadas con una base SQLite en memoria. |

Las operaciones pesadas de CPU (RANSAC) se ejecutan en un hilo con `asyncio.to_thread`, así el
servidor sigue atendiendo otras peticiones mientras calcula.

## Estructura

```
app/
├── main.py                 instancia FastAPI, CORS, middleware de auditoría, manejador de errores 500
├── config.py               configuración centralizada (pydantic-settings)
├── database/connection.py  motor y fábrica de sesiones asíncronas
├── models/space.py         tablas habitaciones y muebles_sugeridos
├── schemas/space.py        esquemas Pydantic de entrada y salida
├── geometry/               octree, colisiones, filtro RANSAC y endpoints /geometry
├── services/omniroute.py   cliente de OmniRoute
├── utils/                  tipos compartidos, ayudas de imagen, logs (logging_config) y middlewares
├── routers/spaces.py       pipeline POST /api/v1/spaces/analyze
└── tests/                  pruebas unitarias y de integración
```

## Puesta en marcha

```
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Documentación interactiva en <http://localhost:8000/docs>. Para las pruebas:
`pip install -r requirements-dev.txt`, luego `pytest` (pruebas) y `ruff check app` (estilo e
importaciones).

Configuración (`app/config.py`): cada variable se lee del entorno o, si existe, del archivo `.env`
de esta carpeta.

| Variable | Descripción |
|---|---|
| `DATABASE_URL` | Cadena de PostgreSQL. Acepta `postgresql://…`; el backend añade el driver asyncpg. |
| `OMNIROUTE_BASE_URL` | Por defecto `http://localhost:20128/v1`. |
| `OMNIROUTE_API_KEY` | Clave de OmniRoute. |
| `OMNIROUTE_MODEL` | Modelo o combo configurado en OmniRoute. |
| `OMNIROUTE_TIMEOUT` | Segundos de espera por respuesta (60 por defecto). |
| `JWT_SECRET` | Clave HS256 de los tokens de sesión. Obligatoria con `ENVIRONMENT=production` (el servidor no arranca sin ella); en desarrollo, si falta, se genera una aleatoria en cada arranque. Cambiarla cierra todas las sesiones. |
| `JWT_EXPIRE_MINUTES` | Vida del token en minutos (60 por defecto). |
| `ENVIRONMENT` | `production` en Render; cualquier otro valor se trata como desarrollo. |
| `ALLOWED_ORIGINS` | Orígenes permitidos por CORS, separados por comas (por defecto `http://localhost:3000`). |
| `ENABLE_DEBUG_ROUTES` | `true` registra `GET /api/v1/spaces/debug-vars`. Apagado por defecto; no definirla en producción. |

Autenticación: todos los endpoints con datos de usuario (`/api/v1/spaces/*` y `/geometry/*`) exigen
`Authorization: Bearer <access_token>` de `POST /api/v1/auth/login`; sin token o con uno inválido o
caducado responden 401. La cuenta es siempre la del `sub` del token. El `user_id` que aún envía el
frontend es opcional: si no coincide con el token, la respuesta es 403. Los recursos de otra cuenta
responden 404, igual que los inexistentes. `/`, `/api/v1/health` y `/api/v1/auth/*` son públicos.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/` | Estado del servidor. |
| POST | `/api/v1/spaces/analyze` | Pipeline completo: RANSAC → OmniRoute → colisiones → PostgreSQL. |
| POST | `/geometry/point-cloud` | Solo el filtro RANSAC sobre una nube de puntos. |
| POST | `/geometry/collision` | Solo la validación de colisiones de una caja. |

Petición mínima de `/api/v1/spaces/analyze`:

```json
{ "nombre": "Sala", "estilo": "nordico", "puntos": [[0.1, 0.0, 2.3], [0.4, 0.0, 1.9], "..."] }
```

Campos opcionales: `instrucciones`, `ancho`/`alto`/`profundidad` (si ya se conocen las medidas) e
`imagen`, una foto de la habitación en base64 o como *data URL*. Con la foto, OpenCV calcula el
brillo y los colores dominantes, que se envían a OmniRoute para que la propuesta armonice con la
habitación y se devuelven en `contexto_visual`.

Convención de coordenadas: metros, eje **y hacia arriba**. La habitación ocupa
`[0, ancho] × [0, alto] × [0, profundidad]`; el origen es la esquina mínima de las superficies
detectadas. En cada mueble, `posicion_x`/`posicion_z` son el centro de su base y `posicion_y` la
altura de esa base.

## Telemetría y auditoría

Cada petición HTTP pasa por `AuditLoggingMiddleware` (`app/utils/middlewares.py`), registrado como
el middleware más externo, así que su medición incluye CORS, la validación de Pydantic y el endpoint
completo (RANSAC, octree, OmniRoute y base de datos).

**Log por petición.** Una línea `clave=valor` por petición, con marca de tiempo UTC al milisegundo
(`app/utils/logging_config.py`):

```
2026-10-12T18:04:05.123Z INFO app.audit method=POST path=/geometry/collision status=200 duration_ms=4.81
2026-10-12T18:04:09.870Z WARNING app.audit method=POST path=/api/v1/spaces/analyze status=502 duration_ms=60012.40
2026-10-12T18:04:11.002Z ERROR app.audit method=GET path=/api/v1/spaces/catalog status=500 duration_ms=3.74
```

| Nivel | Cuándo |
|---|---|
| `INFO` | Respuestas 1xx–4xx. |
| `WARNING` | Respuestas 5xx: `502` (fallo o respuesta inválida de OmniRoute), `503` (base de datos). |
| `ERROR` | Excepción no controlada; además se registra su traza completa con un `request_id`. |

Solo se registra la ruta, nunca el *query string* ni el cuerpo, para no volcar datos del usuario.
La duración se mide con `time.perf_counter()`, un reloj monótono que no salta si se ajusta la hora
del sistema.

**Cabecera `X-Process-Time`.** Toda respuesta incluye el tiempo de proceso del servidor en
milisegundos (`X-Process-Time: 12.34`). Está declarada en `expose_headers` de CORS, así que el
frontend puede leerla aunque se sirva desde otro dominio:

```js
const res = await fetch(`${API}/geometry/collision`, { method: "POST", body, headers });
console.log("servidor:", res.headers.get("X-Process-Time"), "ms");
```

La diferencia entre el tiempo total que mide el navegador y `X-Process-Time` es la latencia de red.

**Errores 500.** Un error que ningún endpoint captura llega al manejador global de `app/main.py`,
que guarda la traza en el log y responde sin detalles técnicos:

```json
{ "detail": "Error interno del servidor.", "error": "internal_server_error", "request_id": "bb83064a8c95" }
```

El `request_id` aparece también en la línea del log (`request_id=bb83064a8c95 Unhandled error in …`)
para localizar el fallo. Esta respuesta lleva su propia cabecera CORS, porque Starlette la genera
fuera de `CORSMiddleware` y sin ella el navegador mostraría un error de CORS en lugar del JSON.

## Estructura de datos: Octree

Un **octree** es un árbol en el que cada nodo representa un cubo del espacio y, cuando hace falta,
se divide en **8 cubos hijos** (octantes) de la mitad de lado. Es la extensión a 3D del quadtree.
Implementación: `app/geometry/octree.py`.

Cada `OctreeNode` guarda:

- `center` y `half_size`: el cubo `[center − half_size, center + half_size]` en los tres ejes.
- `entries`: los puntos (con un dato asociado opcional) si el nodo es hoja.
- `children`: los 8 hijos, o `None` mientras sea hoja.
- `capacity` (8) y `max_depth` (8): cuántos puntos admite una hoja y cuántas veces se puede dividir.

**Particionado.** Al dividir, cada hijo se desplaza `±half_size/2` en x, y, z respecto al centro del
padre. El hijo que corresponde a un punto se calcula con tres comparaciones contra el centro,
que forman un índice de 3 bits:

```
índice = 4·[x ≥ cx] + 2·[y ≥ cy] + 1·[z ≥ cz]        (0 … 7)
```

**Inserción.** Se baja desde la raíz eligiendo el octante con esa fórmula. Si la hoja tiene sitio
(o ya está en `max_depth`) el punto se guarda ahí; si está llena, se divide, reparte sus puntos
entre los hijos y la inserción continúa en el hijo correcto.

**Búsqueda por caja** (`query_box`). Recursiva: un nodo cuyo cubo no se solapa con la caja buscada
se descarta entero junto con todo su subárbol; en las hojas se comprueban los puntos uno a uno.
Esta poda es lo que la hace más rápida que recorrer la nube completa.

**Uso en el sistema.** Tras RANSAC, los puntos que no pertenecen a ninguna superficie (muebles
existentes, objetos y ruido) se insertan en un octree. Para cada mueble propuesto por la IA se
pregunta cuántos de esos puntos caen dentro de su caja; si son 5 o más, hay un objeto real en ese
lugar y el mueble se descarta.

### Complejidad del Octree

Sea `n` el número de puntos, `c` la capacidad de una hoja (8), `D` la profundidad máxima (8) y `k`
el número de puntos devueltos por una búsqueda.

| Operación | Caso típico (puntos repartidos) | Peor caso | Motivo |
|---|---|---|---|
| Elegir octante (`_child_for`) | O(1) | O(1) | Tres comparaciones. |
| `insert` | O(log₈ n) | O(D + c) | Un descenso por nivel; dividir una hoja reparte como mucho `c` puntos. |
| `from_points` (construcción) | O(n log n) | O(n · D) | `n` inserciones. |
| `query_box` | O(log n + k) | O(n) | La poda descarta subárboles; si la caja cubre todo o los puntos se amontonan en hojas de profundidad `D`, se recorren todos. |
| Memoria | O(n) | O(n + 8^D) | Cada punto se guarda una vez; los nodos internos crecen con la profundidad usada. |

`D` es una constante, así que el peor caso de `insert` está acotado: el árbol nunca crece sin
límite aunque muchos puntos caigan en el mismo sitio (esas hojas simplemente admiten más de `c`).

Comparación con la alternativa ingenua: validar `m` muebles recorriendo toda la nube cuesta
O(m · n); con el octree cuesta O(n log n) para construirlo una vez más O(m · (log n + k)) para las
consultas.

### Tiempos medidos del Octree

Promedios de 5 construcciones (`from_points`) y 200 consultas (`query_box`) con cajas del tamaño de
un mueble (0.5 × 0.9 × 0.5 m) sobre puntos uniformes en una habitación de 4 × 3 × 5 m. Medido en un
portátil Intel con Python 3.14; en otra máquina las cifras absolutas cambian, pero no su proporción.

| Puntos `n` | Construcción | Consulta por caja |
|---:|---:|---:|
| 500 | 8 ms | 0.26 ms |
| 2 000 | 36 ms | 0.44 ms |
| 10 000 | 218 ms | 1.0 ms |
| 50 000 | 1.3 s | 3.2 ms |

La construcción crece casi linealmente con `n`, de acuerdo con O(n log n). La consulta crece mucho
más despacio porque la poda descarta la mayor parte del árbol. En `/api/v1/spaces/analyze` el octree
solo guarda los puntos residuales que deja RANSAC (unos cientos en una habitación típica), así que su
coste es de unos pocos milisegundos frente a los 2–3 s de RANSAC. `X-Process-Time` y el log de
auditoría permiten comprobarlo en cada petición.

Para los muebles ya colocados se usa además un `cKDTree` de SciPy sobre sus centros
(construcción O(m log m)): solo los muebles cuya esfera envolvente alcanza la del nuevo se comparan
con la prueba exacta de cajas.

## Algoritmo: RANSAC para detectar planos

Una nube de puntos de cámara web trae ruido: puntos sueltos, reflejos y objetos. Un ajuste por
mínimos cuadrados normal se deja arrastrar por esos valores atípicos. **RANSAC** (*RANdom SAmple
Consensus*) es robusto frente a ellos. Implementación: `app/geometry/ransac_filter.py`.

**Idea.** Repetir `T` veces:

1. Elegir al azar el mínimo de puntos que define un plano (3).
2. Ajustar el plano a esos 3 puntos.
3. Contar los *inliers*: puntos a menos de `residual_threshold` (2 cm) del plano.

Se queda el plano con más inliers y se reajusta con todos ellos.

**Detección de varias superficies.** Se aplica RANSAC, se guardan los inliers del mejor plano como
una superficie, se quitan de la nube y se repite con lo que queda, hasta 6 superficies (suelo,
techo y 4 paredes) o hasta que el mejor plano tenga menos de 50 puntos. Lo que sobra al final son
objetos y ruido (`puntos_residuales`), que alimentan el octree de colisiones.

**Paredes verticales.** `RANSACRegressor` de scikit-learn ajusta una función `y = a·x + b·z + c`,
que no puede representar un plano vertical. Por eso cada ronda prueba los tres ejes como variable
dependiente (`x = f(y, z)`, `y = f(x, z)`, `z = f(x, y)`) y se queda con el que explica más puntos.
La normal del plano sale de los coeficientes: para `y = a·x + b·z + c` es `(−a, 1, −b)` normalizada.
Según su componente vertical se clasifica como `horizontal` (suelo o techo, |n_y| > 0.9),
`pared` (|n_y| < 0.1) o `inclinada`.

**Número de intentos.** Si una fracción `w` de los puntos pertenece al plano buscado, la
probabilidad de que un intento elija 3 inliers es `w³`, y la de acertar al menos una vez en `T`
intentos es `1 − (1 − w³)^T`. Con 6 superficies parecidas, cada eje tiene 2 planos candidatos, así
que por intento la probabilidad es ≈ 2·(1/6)³ ≈ 0.9 %. Con las 100 iteraciones por defecto solo se
acierta el ~61 % de las veces; con `T = 300` (`RANSAC_TRIALS`) sube al ~94 % por eje y a más del
99.9 % combinando los tres ejes.

**Coste.** Por superficie: 3 ejes × `T` intentos × evaluar `s` puntos, con `s = min(n, 5000)`
(`MAX_FIT_POINTS`: el ajuste usa una muestra acotada, pero todos los puntos se puntúan contra el
plano final). En total O(P · 3 · T · s + P · n) para `P` superficies, lineal en `n` para nubes
grandes. Unos 2–3 s para una habitación de ~2 000 puntos, ejecutados en un hilo aparte.

## Arquitectura del flujo con OmniRoute

```
 Navegador (frontend)               Backend FastAPI                              Servicios externos
 ────────────────────               ───────────────                              ──────────────────
 POST /api/v1/spaces/analyze ──▶ routers/spaces.analyze_space
 { nombre, estilo, puntos, imagen? }   │
                                       ├─▶ geometry/ransac_filter ── planos + puntos residuales
                                       │       (hilo de trabajo)
                                       ├─▶ utils/helpers (OpenCV) ── brillo + colores de la foto
                                       │       (en paralelo, solo si se envía imagen)
                                       │
                                       ├─▶ services/omniroute ── POST /v1/chat/completions ──▶ OmniRoute :20128
                                       │       ◀──────── JSON { descripcion, muebles[] } ─────────  │
                                       │                                                        proveedor LLM
                                       │
                                       ├─▶ geometry/collision + Octree ── muebles válidos / descartados
                                       │
                                       ├─▶ SQLAlchemy + asyncpg ─────────────────────────────▶ PostgreSQL
                                       │       habitaciones 1 ── N muebles_sugeridos
                                       ▼
 201 { habitacion, muebles,  ◀── AnalyzeResponse
       descartados, superficies,
       contexto_visual }
```

Errores: `502` si OmniRoute falla o responde algo que no es JSON válido, `503` si no se puede
guardar en la base de datos y `422` si la petición o la imagen son inválidas o no se pueden deducir las
dimensiones de la habitación.
