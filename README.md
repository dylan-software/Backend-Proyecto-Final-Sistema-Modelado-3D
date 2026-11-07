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
├── main.py                 instancia FastAPI, CORS, middleware de auditoría, manejadores de errores 422 y 500
├── config.py               configuración centralizada (pydantic-settings)
├── database/connection.py  motor y fábrica de sesiones asíncronas
├── models/space.py         tablas habitaciones y muebles_sugeridos
├── schemas/space.py        esquemas Pydantic de /analyze, /catalog y /spaces (snake_case)
├── schemas/room.py, chat.py esquemas de salas, layout y chat (JSON en camelCase)
├── geometry/               octree, colisiones, RANSAC, sala poligonal (room.py), layout y endpoints /geometry
├── services/omniroute.py   cliente de OmniRoute
├── services/scene_assistant.py  contexto y prompt del chat sobre una sala
├── utils/                  tipos compartidos, ayudas de imagen, logs (logging_config) y middlewares
├── routers/spaces.py       POST /analyze, catálogo, listado, POST /rooms/{id}/analyze-photo y /photo-undo
├── routers/rooms.py        salas poligonales editables, sus muebles y el layout
├── routers/chat.py         POST /api/v1/chat
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
| `AI_PHOTO_TIMEOUT` | Tiempo límite TOTAL, en segundos, del análisis de una foto con visión (45 por defecto, entre 1 y 300). Al agotarse, `analyze-photo` responde 504. |
| `JWT_SECRET` | Clave HS256 de los tokens de sesión. Obligatoria con `ENVIRONMENT=production` (el servidor no arranca sin ella); en desarrollo, si falta, se genera una aleatoria en cada arranque. Cambiarla cierra todas las sesiones. |
| `JWT_EXPIRE_MINUTES` | Vida del token en minutos (60 por defecto). |
| `ENVIRONMENT` | `production` en Render; cualquier otro valor se trata como desarrollo. |
| `ALLOWED_ORIGINS` | Orígenes permitidos por CORS, separados por comas (por defecto `http://localhost:3000`). |
| `ENABLE_DEBUG_ROUTES` | `true` registra `GET /api/v1/spaces/debug-vars`. Apagado por defecto; no definirla en producción. |
| `MAX_CATALOG_ITEMS_PER_USER` | Máximo de muebles en el catálogo de una cuenta (300 por defecto). Al llegar, `POST /catalog` responde 409 sin llamar al modelo. |
| `COHERENCE_CACHE_SIZE` | Nombres de mueble cuya respuesta del validador de coherencia se recuerda en memoria (1000 por defecto; caché LRU). Repetir un nombre en `POST /catalog` no vuelve a llamar al modelo. Se pierde al reiniciar. |
| `MAX_GROUPS_PER_USER` | Máximo de grupos de proyectos de una cuenta (50 por defecto). Al llegar, `POST /groups` responde 409. |
| `AI_REQUESTS_PER_MINUTE`, `AI_REQUESTS_PER_DAY` | Llamadas a la IA por cuenta (10 por minuto y 200 por día por defecto), sumando chat, catálogo, `/analyze` y `analyze-photo` (cada foto cuenta como 3). Más allá, 429 con `Retry-After`. |
| `LOGIN_FAILURES_PER_EMAIL`, `LOGIN_FAILURE_WINDOW_MINUTES` | Intentos fallidos de login por email (10 en 15 minutos por defecto). Más allá, 429 con `Retry-After`. |
| `REGISTRATIONS_PER_HOUR`, `REGISTRATIONS_PER_IP_PER_HOUR` | Registros por hora en total (20) y por IP de cliente (5, de mejor esfuerzo). Más allá, 429 con `Retry-After`. |
| `TRUSTED_PROXY_HOPS` | Proxies propios delante del servidor que añaden entradas a `X-Forwarded-For` (0 por defecto: se ignora la cabecera). En Render, `1` (ya en `render.yaml`). |

Autenticación: todos los endpoints con datos de usuario (`/api/v1/spaces/*`, `/api/v1/chat` y `/geometry/*`) exigen
`Authorization: Bearer <access_token>` de `POST /api/v1/auth/login`; sin token o con uno inválido o
caducado responden 401. La cuenta es siempre la del `sub` del token. El `user_id` que aún envía el
frontend es opcional: si no coincide con el token, la respuesta es 403. Los recursos de otra cuenta
responden 404, igual que los inexistentes. `/`, `/api/v1/health` y `/api/v1/auth/*` son públicos.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/` | Estado del servidor. |
| POST | `/api/v1/spaces/analyze` | Pipeline completo: RANSAC → OmniRoute → colisiones → PostgreSQL. |
| POST | `/api/v1/spaces/rooms` | Crea una sala poligonal: `{name, vertices: [{x, y}], heightM, groupId?, description?}` → 201 `{id, name, vertices, heightM, areaM2}` (más `groupId` y `description` si tienen valor). 404 si `groupId` es de otra cuenta. |
| GET | `/api/v1/spaces/rooms/{id}` | La sala; las creadas por `/analyze` vuelven como su rectángulo. |
| PATCH | `/api/v1/spaces/rooms/{id}` | Renombra, describe o mueve de grupo un proyecto: `{name?, description?, groupId?}` (al menos uno; `groupId: null` lo deja sin grupo; `description` `null` o `""` la borra). No toca forma, altura ni muebles. |
| GET | `/api/v1/spaces/groups` | Grupos de la cuenta, del más antiguo al más nuevo: `{groups: [{id, name, description, createdAt, projectCount}]}`. |
| POST | `/api/v1/spaces/groups` | Crea un grupo: `{name, description?}` → 201. 409 al llegar a `MAX_GROUPS_PER_USER`. |
| PATCH | `/api/v1/spaces/groups/{id}` | Cambia `name` o `description` (al menos uno; `description` `null` o `""` la borra). |
| PATCH | `/api/v1/spaces/rooms/{id}/vertices/{index}` | Mueve un vértice: `{x, y}`. 422 si el polígono queda inválido (la sala no cambia). |
| POST | `/api/v1/spaces/rooms/{id}/vertices` | Inserta un vértice: `{afterIndex, x, y}`. |
| DELETE | `/api/v1/spaces/rooms/{id}/vertices/{index}` | Elimina un vértice; una sala conserva al menos 3. |
| GET | `/api/v1/spaces/rooms/{id}/items` | Muebles colocados: `{items: [{id, name, x, y, rotation, widthM, depthM, heightM, elevationM}]}`. |
| POST | `/api/v1/spaces/rooms/{id}/layout` | Distribuye el catálogo dentro del polígono real (sin IA) y guarda el resultado. |
| POST | `/api/v1/spaces/rooms/{id}/analyze-photo` | Multipart (`file`, `confirm_replace`, `instrucciones`): la IA con visión estima la sala y hasta 10 objetos, que reemplazan la forma y los muebles de la sala. |
| POST | `/api/v1/spaces/rooms/{id}/photo-undo` | Sin cuerpo: devuelve la sala a como estaba antes de su última foto → `{room, items, remaining}`. 409 si no hay nada que deshacer. |
| POST | `/api/v1/chat` | Chat sobre una sala: `{roomId, messages}` → `{role: "assistant", content}`. |
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

Grupos de proyectos (migración 005 del repo database; el backend la aplica al arrancar en PostgreSQL,
RLS de `grupos_proyecto` incluido): un grupo es una carpeta de proyectos con una descripción libre, y cada
proyecto (sala) puede tener grupo y descripción. Nombres en texto plano sin espacios en los extremos (1-80
caracteres el grupo, 1-120 el proyecto; sin `<`, `>` ni caracteres de control); descripciones de hasta 1000
caracteres, con saltos de línea y tabuladores; vacías se guardan como `null`. Los 422 no repiten la
entrada, lo ajeno responde 404 y nada de esto gasta cuota de IA. `GET /api/v1/spaces` añade `group_id` y
`description` a cada sala. No hay borrado de grupos todavía; `habitaciones.derivado_de` está reservado.

Convención de coordenadas: metros, eje **y hacia arriba**. La habitación ocupa
`[0, ancho] × [0, alto] × [0, profundidad]`; el origen es la esquina mínima de las superficies
detectadas. En cada mueble, `posicion_x`/`posicion_z` son el centro de su base y `posicion_y` la
altura de esa base.

## Salas poligonales, layout, chat y foto

Contrato JSON en camelCase (`heightM`, `areaM2`, `afterIndex`); también se aceptan las claves en
snake_case. Todo en metros salvo el catálogo, que está en centímetros: la conversión se hace en un solo
lugar, `app/geometry/units.py`.

- **Salas.** Un polígono de al menos 3 vértices, sin autointersecciones y con área de al menos
  0.01 m². La `y` de cada vértice es el eje z en 3D. Cada edición revalida la forma; si queda inválida,
  la respuesta es 422 y la sala no cambia. `GET /api/v1/spaces` sigue sin incluir `vertices`: se leen
  con `GET /rooms/{id}`.
- **Posición de un mueble.** La API usa la **esquina mínima** de su huella (`x`, `y`); la base de datos
  guarda el **centro** (`posicion_x`, `posicion_z`). Las conversiones están en `app/geometry/layout.py`.
- **Layout.** `POST /rooms/{id}/layout` con `{constraints: {minDistanceBetweenItemsCm, minClearPathCm},
  itemIds?}`. Lee la sala y el catálogo de la cuenta desde la base (nunca la geometría del cliente),
  empaqueta los muebles en filas dentro del polígono real (desplazándolos de 10 en 10 cm si no caben;
  si no caben en su fila, busca un hueco en las filas siguientes, de 10 en 10 cm en y, que respete la
  distancia mínima con todo lo ya colocado) y valida colisiones entre cajas: el solape es el **menor**
  de los solapes por eje, con 2 cm de tolerancia. `minDistanceBetweenItemsCm` se aplica entre muebles y
  al borde de la caja delimitadora de la sala, no a las paredes interiores: un mueble puede quedar
  pegado a la pared de una muesca. La búsqueda está acotada para que la sala más grande que acepta la
  API (±1000 m, 200 vértices, 200 muebles) responda en torno a medio segundo: en salas de más de 10 m
  de lado el paso crece hasta dar como mucho 100 posiciones por eje, y hay un tope de trabajo por
  mueble y por petición. Un mueble que agota su tope sale con la advertencia "Se detuvo la búsqueda
  de un hueco…"; al agotarse el de la petición, una sola advertencia lista los muebles que no se
  colocaron. Corre en un hilo aparte para no bloquear el servidor. Responde
  `{layout: {positions, warnings}, validation, lighting, readyToRender, errors}`.
  Si `readyToRender` es true, las posiciones reemplazan los muebles anteriores de la sala.
  `minClearPathCm` se acepta pero no se usa: todavía no se guardan puertas.
- **Chat.** El servidor arma el contexto (vértices, altura, área y muebles) tras verificar el dueño y lo
  pone en el system prompt entre delimitadores, como datos no confiables. El asistente no recibe
  imágenes. Sin bucle de tool-calling, así funciona con cualquier modelo de OmniRoute. Máximo 20
  mensajes de 2000 caracteres; los errores de OmniRoute responden 502.
- **Foto a 3D.** `POST /rooms/{id}/analyze-photo` (multipart). Contrato completo en
  `CONTRATO-FOTO-A-3D.md`, en la carpeta que contiene los tres repos.
  - Campos: `file` (`image/jpeg` o `image/png`, los primeros bytes deben coincidir con el tipo
    declarado, hasta 5 MB), `confirm_replace` (`false` por defecto) e `instrucciones` (opcional,
    máximo 500 caracteres). Las instrucciones llegan al modelo como un valor JSON entre delimitadores:
    son datos, no órdenes.
  - La foto va una sola vez al modelo con visión (`image_url` con un *data URL* en base64, por el mismo
    `/chat/completions`) y se descarta: no se guarda ni se registra. Ni la foto, ni su base64, ni las
    instrucciones, ni la salida del modelo aparecen en logs o en mensajes de error.
  - La respuesta del modelo se valida con un esquema estricto (`app/schemas/photo.py`): sala de 1,5 a
    12 m por lado y de 2 a 4 m de alto, objetos de 5 a 400 cm por lado, nombres de texto plano de
    hasta 60 caracteres, sin NaN ni infinitos y sin campos desconocidos. Un objeto inválido se descarta
    con su motivo; uno que se sale de la sala hasta 10 cm se mete dentro; se aceptan como mucho 10.
  - Coherencia espacial (`app/geometry/arrangement.py`, determinista; los solapes ya NO se aceptan):
    - Puertas, ventanas, espejos y cuadros (`kind="wall"`) se pegan a la pared más cercana, paralelos
      a ella; en una pared vertical se intercambian ancho y fondo. La puerta (por su nombre: "puerta" o
      "door") va al suelo; el resto, entre el suelo y el techo, y por encima del mueble en el que se
      metan (el espejo sobre la cama).
    - Los objetos de suelo se procesan de mayor a menor área. Uno pequeño (menos de 0,6 m de alto y del
      40 % del área) con el centro dentro de otro que está en el suelo va encima (el portátil sobre la
      cama); nunca sobre algo ya apilado. Si no, se desplaza lo mínimo a un lado del que estorba,
      dentro de la sala, o se descarta con el motivo `"se solapa con <nombre>"`.
    - Apoyado encima o pegado al lado no cuenta como solape. El prompt pide los nombres en español.
  - Sin migración: la sala pasa a ser el rectángulo estimado (`vertices`, `ancho`, `profundidad`,
    `alto`) y los objetos reemplazan sus muebles (centro en `posicion_x`/`posicion_z`, base en
    `posicion_y`), en una sola transacción.
  - Respuesta 200: `{room, estimated: true, description, items, discarded: [{name, reason}]}`.
    `estimated` no se guarda. Los `items` traen las medidas para dibujar cada objeto como una caja.
  - Errores: 401; 404 sala inexistente o ajena; **409** si la sala ya tiene forma o muebles y falta
    `confirm_replace=true` (no lee la foto ni gasta cupo); 413; 415; 422 imagen no decodificable
    (texto fijo) o formulario inválido (sin eco de la entrada); 429 (la foto cuesta 3 llamadas);
    **502** si la IA falla o no devuelve una estimación válida; 503; **504** si la IA no responde en
    `AI_PHOTO_TIMEOUT` segundos ("El análisis de la foto tardó demasiado…"). En 502 y 504 la sala no
    cambia.
  - FastAPI parsea el multipart antes de autenticar, así que `BodySizeLimitMiddleware` rechaza con 413
    un cuerpo de más de 5 MB + 64 KB antes de parsearlo, haya `Content-Length` o venga en chunks.
- **Deshacer la foto.** `POST /rooms/{id}/photo-undo`, sin cuerpo. Contrato completo en
  `CONTRATO-DESHACER-FOTO.md`, en la carpeta que contiene los tres repos.
  - Antes de aplicar una foto se copia lo que va a sobrescribir (`vertices`, también si es `null`,
    `ancho`, `profundidad`, `alto` y los muebles con sus medidas y su centro). La copia se apila solo
    si el guardado de la foto sale bien: una foto con 409, 413, 415, 422, 429, 502, 503 o 504 no deja
    nada que deshacer.
  - `app/services/photo_undo.py`: una `LinkedStack` de capacidad 5 por sala (con la pila llena se
    descarta la copia más antigua, pasando las demás por una pila auxiliar) dentro de una `LruCache`
    de 200 salas con clave `(cuenta, sala)`.
  - Respuesta 200: `{room, items, remaining}`, con `room` e `items` como en `analyze-photo`;
    `remaining` es cuántas fotos más se pueden deshacer. Errores: 401; 404 sala inexistente o ajena
    (antes de mirar la pila); **409** si no queda ninguna foto que deshacer; 503/500 si falla la base
    de datos, y entonces la copia vuelve a la pila.
  - No llama a la IA ni gasta cuota: funciona aunque la cuota esté agotada. No hay Rehacer: repetir
    una foto deshecha vuelve a costar 3 llamadas.
  - Deshacer vuelve exactamente al estado de antes de la foto. Dos fotos simultáneas en la misma sala
    guardan el mismo estado previo.
  - **El historial vive en memoria**, como los límites de peticiones: se pierde si Render reinicia el
    servidor (entonces responde 409 explicándolo) y necesita un único worker de uvicorn.

## Límites de peticiones

Los contadores viven en memoria (`app/utils/rate_limit.py`, ventanas deslizantes, sin dependencias):
se pierden al reiniciar y no se comparten entre procesos. La imagen Docker arranca un único worker de
uvicorn en una instancia de Render; con varios workers, cada uno permitiría el límite completo. El
historial de Deshacer de la foto también vive en memoria con la misma condición.
Superar un límite responde 429 con la cabecera `Retry-After` en segundos. CORS la expone
(`expose_headers`), así que el frontend puede leerla desde otro dominio y decir cuánto esperar.

- **IA, por cuenta.** Chat, `POST /catalog`, `/analyze` y `analyze-photo` comparten un cupo de 10
  llamadas por minuto y 200 por día. Una foto cuenta como 3 llamadas (3 fotos por minuto como mucho).
  Solo cuenta la petición que llega a llamar al modelo: un 404, un 409, un 415, un 422 o un catálogo
  lleno no gastan cupo; un 502 o un 504 sí, porque la llamada ya se hizo.
- **Login, intentos fallidos por email.** Tras 10 fallos de un email en 15 minutos, ese email responde
  429 sin comprobar la contraseña (aunque sea la correcta) hasta que el fallo más antiguo sale de la
  ventana; un login correcto borra sus fallos. Se cuenta por email y no por IP porque la IP llega en
  cabeceras que el cliente puede falsificar. Contrapartida: cualquiera puede bloquear un email durante
  esa ventana fallando a propósito; el dueño vuelve a entrar cuando termina.
- **Registro.** Como mucho 20 intentos por hora en total y 5 por IP de cliente; un intento con un email
  ya registrado también cuenta. El límite global no depende de ninguna cabecera.

**La IP detrás del proxy de Render.** `X-Forwarded-For` es una lista que empieza el cliente: puede enviar
`X-Forwarded-For: 1.2.3.4` y cada proxy añade al final la IP que vio. Solo son fiables las entradas que
añaden nuestros propios proxies, que son las **últimas** `TRUSTED_PROXY_HOPS`; la primera nunca se usa.
Con `0` se ignora la cabecera y se usa el par TCP. uvicorn arranca con `--no-proxy-headers` (en el
`Dockerfile` y en `start.sh`) para que no reescriba él mismo la IP del cliente a partir de
`X-Forwarded-For`: sin esa opción confía en las peticiones que llegan desde `127.0.0.1` y un cliente local
podría cambiar de IP con cada petición. Así solo `TRUSTED_PROXY_HOPS` decide la IP, y en Render el par TCP
es el proxy de Render, no el usuario; por eso `render.yaml` pone
`TRUSTED_PROXY_HOPS=1`, **suponiendo** que el proxy de Render añade la IP real del cliente como última
entrada. Esa suposición no está verificada: si fuera falsa, el límite por IP solo sería más débil (todas
las peticiones contarían como una IP, o como la IP que el cliente escriba si Render no añadiera nada), y
el límite global seguiría intacto. Por eso el login se limita por email y no por IP.

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
