# Defensa del proyecto: estructuras de datos, arquitectura y seguridad

Aplicación web de decoración de espacios en 3D con medidas reales (proyecto final de Estructura de Datos).
El usuario crea proyectos (salas), sube una foto que una IA convierte en una sala con objetos dibujados como
cajas en 3D, chatea con un asistente que conoce la sala y puede deshacer la última foto.

Estado al escribir este documento: rama `dev`, backend en `dc9fb03`. Las cifras de las pruebas son de esa
versión (ver la sección 5).

---

## 1. Arquitectura

```
 Navegador (escritorio o móvil)
   │  HTTPS, JSON y multipart; sesión en la cabecera Authorization: Bearer <JWT>
   ▼
 Frontend: Next.js + TypeScript + React Three Fiber              -> desplegado en Vercel
   │  una sola variable pública: la dirección de la API
   ▼
 Backend: FastAPI (Python), un worker de uvicorn                 -> desplegado en Render (Docker)
   │  ├── app/routers/     endpoints /api/v1 (auth, spaces, rooms, groups, chat)
   │  ├── app/structures/  las estructuras de datos hechas a mano (sección 2)
   │  ├── app/utils/       límites de peticiones (cola), middlewares, logs
   │  ├── app/services/    cliente de IA, caché de coherencia (LRU), deshacer la foto (pila)
   │  └── app/geometry/    sala poligonal, layout, colisiones, octree
   │
   ├── SQLAlchemy asíncrono + asyncpg ──> PostgreSQL en Supabase
   │                                      (esquema en el repo database: schema.sql, verify.sql, migrations/)
   └── HTTPS ──> proveedor de IA externo (Gemini, endpoint compatible con OpenAI); la clave vive solo en Render
```

- **Tres repositorios:** `proyecto-3d-database` (SQL), `proyecto-3d-frontend` (Next.js) y
  `proyecto-3d-backend` (FastAPI).
- **Solo el backend habla con la base de datos.** El frontend nunca tiene credenciales de la base ni de la IA.
- **Estado en memoria del proceso:** los límites de peticiones, la caché de coherencia y las pilas de
  deshacer viven en la memoria del único worker. Se pierden si Render reinicia el servicio y no se
  comparten entre workers ni instancias. Es una decisión consciente (decisión 24) y está documentada en el
  README.

---

## 2. Estructuras de datos propias (`app/structures/`)

Todas están hechas a mano con nodos y referencias. `list`, `deque` y `OrderedDict` **no** son su contenedor
interno; solo se usan en las pruebas como referencia para comparar resultados. La única excepción es
`FixedArray`, que por definición es un arreglo estático sobre una `list` reservada una sola vez y que nunca
crece ni encoge.

Rasgos comunes:
- Cada clase documenta en su docstring el coste Big-O de cada operación.
- Capacidad opcional: superarla lanza `CapacityError` (`app/structures/exceptions.py`).
- `check_invariants()` comprueba la consistencia interna (punteros recíprocos, tamaño, ciclos, capacidad) y
  lanza `InvariantError`. Las pruebas la llaman después de cada operación.
- `__slots__` en los nodos, para que cada nodo ocupe menos memoria.

### 2.1 Resumen

| Estructura | Clase (archivo) | Operaciones principales y coste | ¿Integrada? Dónde |
|---|---|---|---|
| Cola FIFO | `LinkedQueue` + `QueueNode` (`linked_queue.py`) | `enqueue`, `dequeue`, `peek`, `peek_back`, `len`: O(1). Recorrer, `clear`: O(n) | **Sí:** limitador de peticiones a la IA y de login/registro (`app/utils/rate_limit.py`) |
| Lista doblemente enlazada | `DoublyLinkedList` + `DoublyNode` (`doubly_linked_list.py`) | `push_front/back`, `pop_front/back`, `remove(nodo)`, `move_to_front(nodo)`: O(1). Recorrer: O(n) | **Sí, como base de `LruCache`** |
| Caché LRU | `LruCache` (`lru_cache.py`) = `DoublyLinkedList` + `dict` | `get`, `put`, `discard`, `peek`, expulsar el menos usado: O(1) promedio | **Sí:** validador de coherencia de `POST /catalog` y almacén de pilas de deshacer |
| Pila LIFO | `LinkedStack` + `StackNode` (`linked_stack.py`) | `push`, `pop`, `peek`, `len`: O(1). Recorrer, `clear`: O(n) | **Sí:** Deshacer de cada sala: foto, chat, objetos, layout y vértices (`app/services/room_undo.py`) |
| Arreglo estático | `FixedArray` (`fixed_array.py`) | `get`, `set`, `a[i]`: O(1). `fill`, `index_of`, recorrer: O(n) | **Sí:** celdas del suelo y franja ocupada de cada pared al colocar objetos (`app/geometry/arrangement.py`; ver 2.6) |
| Lista circular doble | `CircularDoublyLinkedList` + `RingNode` (`circular_doubly_linked_list.py`) | `append`, `insert_after(nodo)`, `remove(nodo)`: O(1). `get/set/insert/remove_at`: O(min(i, n−i)). `rotate(k)`: O(min(k, n−k)) | **Sí:** vértices y paredes de `Room` (`app/geometry/room.py`; ver 2.7) |
| Árbol n-ario | `NaryTree` + `TreeNode` (`nary_tree.py`) | `add_child`, hijos, `len`: O(1). `remove(nodo)`: O(k) del subárbol. `walk`/`depth_first`/`count`: O(k). `depth`: O(h) | **Sí:** grupos y proyectos en `GET /groups` (`app/services/project_tree.py`; ver 2.8) |

### 2.2 Cola: `LinkedQueue` en el limitador de peticiones

**Clase.** Lista simplemente enlazada con un puntero a la cabeza (frente, el siguiente en salir) y otro a la
cola (final, el último en entrar). `enqueue` engancha al final y `dequeue` suelta el frente, ambos en O(1)
sin mover ningún otro elemento.

**Dónde se usa.** `RateLimiter` en `app/utils/rate_limit.py` guarda, por cada clave (cuenta, email o IP), una
`LinkedQueue` con las marcas de tiempo de sus peticiones, la más antigua al frente. Es una **ventana
deslizante**:
- `record` encola la marca actual por el final: O(1).
- `_recent` desencola por el frente las marcas que ya salieron de la ventana: O(1) cada una. Como cada marca
  entra y sale una sola vez, el coste amortizado es O(1) por petición.
- `retry_after` recorre la cola para contar las marcas dentro de cada ventana y calcular el `Retry-After`.

**Qué protege.**
- La cuota de IA por cuenta (`AI_REQUESTS_PER_MINUTE=10` y `AI_REQUESTS_PER_DAY=200`), compartida por chat,
  catálogo, análisis y foto. La foto cuesta 3.
- Los fallos de login por email.
- Los registros, en total y por IP.

Al pasarse, la respuesta es **429** con la cabecera `Retry-After`.

**Por qué una cola.** El patrón de acceso es exactamente FIFO: entra la marca más nueva y caduca la más
vieja. Una `list` de Python cuesta O(n) al quitar por el frente; la cola enlazada cuesta O(1).

**Memoria acotada.** Cada cola solo crece hasta el límite de su ventana, porque se registra después de
comprobar que la petición cabe. El número de claves está acotado por `MAX_KEYS = 50 000`: al llegar, se
olvidan primero las claves inactivas.

**Pruebas.** `test_linked_queue.py` (23 casos) y `test_rate_limit_queue.py` (7 casos). Estos últimos comprueban
que el limitador guarda `LinkedQueue` y responde lo mismo que una referencia hecha con listas. La suite
anterior de `test_rate_limit.py` sigue pasando sin cambios.

### 2.3 Lista doble + diccionario: `LruCache` en el validador de coherencia

**Clase.** `LruCache` combina dos piezas:
- un `dict` clave → nodo, para encontrar una entrada en O(1);
- una `DoublyLinkedList` de pares `(clave, valor)` ordenada por uso: el frente es lo más reciente y el final
  lo más antiguo.

Con el nodo en la mano, `move_to_front` y `pop_back` son O(1). Por eso `get`, `put` y la expulsión del menos
usado son O(1). Cada nodo sabe a qué lista pertenece (`owner`), así que pasar un nodo ajeno lanza
`ValueError`. `get` cuenta aciertos (`hits`) y fallos (`misses`).

**Dónde se usa (1): validador de coherencia.** Al guardar un mueble con `POST /catalog`, una IA decide si el
nombre es un mueble coherente. `cached_furniture_coherence` (`app/services/semantic_filter.py`) pone una
`LruCache` delante. `get_coherence_validator` en `app/routers/spaces.py` la devuelve.
- **Clave:** el nombre normalizado (Unicode NFC, espacios colapsados, `casefold`). Así, `"  Sofá  "` y
  `"sofá"` comparten clave y las tildes se conservan.
- **Valor:** solo `True` o `False`, **nunca errores**. Si la IA falla, no se guarda nada y el siguiente intento
  vuelve a preguntar.
- **Capacidad:** `COHERENCE_CACHE_SIZE`, 1000 por defecto.
- **Efecto medible:** la segunda vez que se guarda "Sofa" no se llama al modelo. La petición sigue contando
  en la cuota de IA, así que el contrato de la API no cambia.

**Dónde se usa (2): almacén de pilas de deshacer.** Ver 2.4. La LRU limita a 100 las salas con historial.

**Pruebas.** `test_doubly_linked_list.py` (25 casos), `test_lru_cache.py` (20 casos, contra una LRU de
referencia hecha con `OrderedDict` solo en la prueba) y `test_coherence_cache.py` (12 casos de la integración).

### 2.4 Pila: `LinkedStack` para el Deshacer de cada sala

**Clase.** Lista simplemente enlazada cuya cabeza es la cima. Cada `StackNode` apunta al de debajo. `push` y
`pop` son O(1).

**Dónde se usa.** `POST /api/v1/spaces/rooms/{room_id}/undo`, en `app/routers/rooms.py` (`photo-undo` es un
alias obsoleto). La lógica está en `app/services/room_undo.py`. Hay **una sola pila por sala** para todos
sus cambios, no una por tipo de cambio.
- Antes de cada cambio guardado de la sala se toma una copia **inmutable** (`RoomSnapshot`, una `dataclass`
  congelada): vértices, ancho, profundidad, alto y los objetos con sus medidas y su posición, más `change`,
  el cambio que vino después. No guarda referencias a filas de SQLAlchemy. Apilan la foto a 3D, cada
  mensaje del chat con acciones (una copia por mensaje, aunque tenga 3 acciones), añadir, cambiar o borrar
  un objeto, el layout guardado y la edición de vértices.
- La copia **se apila solo si el commit sale bien** (`commit_with_undo`, el único punto donde se guarda un
  cambio con Deshacer). Un cambio rechazado o fallido no apila nada.
- Como la pila es LIFO, Deshacer siempre quita **el último cambio**, sea del tipo que sea: deshacer una foto
  nunca puede borrar el trabajo hecho después, porque antes hay que deshacer ese trabajo.
- `RoomUndoStore` es una `LruCache` de 100 salas con clave `(user_id, room_id)`. Cada valor es una
  `LinkedStack` de capacidad 50.
- **Pila llena:** `LinkedStack` lanza `CapacityError`. Para descartar la copia más antigua sin añadir
  operaciones a la clase, `_drop_oldest` usa una **segunda pila auxiliar**: pasa todo a la auxiliar, quita su
  cima (que era el fondo) y devuelve el resto. Cuesta O(n) con n ≤ 50. La razón de descartar en lugar de
  rechazar está en 2.4.1.
- **Memoria acotada:** además, las copias de una sala suman como mucho 2000 objetos; si se pasan, también se
  descartan las más antiguas. Peor caso: 100 salas × 2000 objetos ≈ 60 MB. Sin ese tope serían 100 × 50 ×
  200 objetos ≈ 300 MB, demasiado para el plan Starter de Render.
- **Deshacer:** desapila (sin `await` entre leer y quitar), restaura con una lista de vértices **nueva**,
  hace commit y responde `{room, items, remaining, undone}`. Si el commit falla, la copia **vuelve a la
  pila**. Los objetos se recrean desde la copia, así que sus `id` cambian.

#### 2.4.1 Por qué, con la pila llena, se descarta la copia más antigua en vez de rechazar el cambio

El plan inicial decía "con la pila llena se rechaza el cambio". Se eligió lo contrario, descartar la copia
más antigua (como hacen los editores de texto o de imagen), por estas razones:

1. **Rechazar bloquearía la aplicación, no solo el Deshacer.** Con una sola pila por sala, cada cambio
   apila una copia. Si la pila llena rechazara el cambio, tras 50 cambios la sala quedaría congelada: no se
   podría hacer otra foto, ni pedir nada al chat, ni añadir, mover o borrar un objeto. El usuario no ha
   hecho nada mal; solo ha trabajado mucho en la misma sala.
2. **Para desbloquear habría que perder trabajo.** La única salida sería deshacer algo para liberar sitio.
   Como no hay Rehacer, eso borra de verdad el último cambio, que suele ser el que el usuario quiere
   conservar. Descartar la copia más antigua solo pierde la posibilidad de volver a un estado de hace más de
   50 cambios, que casi nunca se usa.
3. **El valor de un historial está en los cambios recientes.** Deshacer sirve sobre todo para corregir lo
   último que salió mal. Las copias más antiguas son las que menos valor tienen y las únicas que se pierden.
4. **El tope de memoria necesita descartar de todos modos.** El límite de 2000 objetos por sala no puede
   rechazar cambios (una sala de 200 objetos lo alcanzaría en 10 cambios). Si la pila llena rechazara y el
   tope de memoria descartara, habría dos políticas para el mismo problema. Con descarte, la regla es una
   sola: siempre se conserva lo más reciente.
5. **No cambia la estructura ni su coste.** La pila sigue siendo una `LinkedStack` pura: `push` y `pop` en
   O(1) y `CapacityError` al llenarse, que es lo que garantiza el límite. El descarte lo hace el almacén con
   una pila auxiliar (O(n), n ≤ 50, solo cuando la pila está llena), sin añadir a la clase operaciones que
   no son de pila. Así se demuestran las dos cosas: el tope de la estructura y cómo se respeta sin bloquear
   al usuario.
6. **Ya estaba probado.** Es la misma política que tenía el Deshacer de la foto (con 5 copias), así que se
   reutiliza código probado en lugar de introducir un comportamiento nuevo.

Contrapartida aceptada: tras más de 50 cambios en la misma sala no se puede volver al estado inicial con
Deshacer. Se documenta, y la respuesta `remaining` dice siempre cuántos cambios quedan.

**Errores.**

| Código | Cuándo |
|---|---|
| 401 | Sin token o token inválido |
| 404 | Sala inexistente o ajena; se comprueba antes de mirar la pila |
| 409 | No hay nada que deshacer, o la pila se perdió por un reinicio |
| 503 / 500 | Fallo de la base de datos |

No gasta cuota de IA.

**Frontend.** El botón "Deshacer foto" (`PhotoUndoButton.tsx`) aparece junto a "Foto a 3D" después de una
foto hecha en la visita actual a la página (pendiente de pasar a un único botón "Deshacer").

**Pruebas.** `test_linked_stack.py` (20 casos) y `test_room_undo.py` (34 casos del almacén y de la API),
más los casos de Deshacer de `test_room_objects.py`, `test_room_items_routes.py` y `test_chat.py`.
- Orden LIFO con dos fotos y entre tipos de cambio (foto → objeto → deshacer el objeto y luego la foto).
- Tope de 50 y tope de objetos: se descartan las más antiguas.
- Un mensaje del chat con 3 acciones se deshace de una vez.
- 404 de una sala ajena sin tocar la pila del dueño.
- 409 tras simular un reinicio.
- Funciona con la cuota agotada.
- Un fallo de commit devuelve la copia a la pila.
- Releer con una sesión nueva devuelve exactamente lo de antes.

### 2.5 Árbol: `OctreeNode` (anterior a este bloque)

El backend ya tenía un octree (`app/geometry/octree.py`). Cada nodo es un cubo del espacio que se divide en
8 hijos. Se usa en `/api/v1/spaces/analyze` para detectar colisiones entre los muebles propuestos y los
puntos de la escena: O(n log n) para construirlo y O(log n + k) por consulta, frente a O(m·n) por fuerza
bruta. Su complejidad y sus tiempos medidos están en la sección "Estructura de datos: Octree" del README.

### 2.6 `FixedArray`: celdas del suelo y franja ocupada de cada pared

**Clase.** Reserva `size` casillas una sola vez en el constructor y nunca crece ni encoge: no tiene `append`,
`insert` ni `pop`.
- `get`, `set` y `a[i]` son O(1) y comprueban los límites.
- Un índice fuera de `[0, size)` lanza `IndexError`, incluidos los negativos, que **no** cuentan desde el
  final.
- Un índice que no es entero (un `bool` o un `slice`) lanza `TypeError`.

**Dónde se usa.** En `app/geometry/arrangement.py`, que coloca los objetos de la foto, del formulario, del
chat y de los movimientos a mano:
- **Celdas del suelo (`FloorCells`).** La caja de la sala se parte en una rejilla fija de 16 × 16 celdas,
  guardada en un `FixedArray` de 256 casillas con índice `fila*16+columna`. Cada casilla tiene la lista de
  objetos cuya huella toca esa celda (con una celda de margen). `first_free_floor_spot` compara cada posición
  candidata solo con los objetos de las celdas que toca, en vez de con todos.
- **Franja de la pared (`wall_strip`).** Un objeto de pared solo se desliza a lo largo de su pared, así que
  solo puede chocar con los objetos que cruzan su franja. Esos objetos (los tramos ocupados de la pared) se
  copian a un `FixedArray` del tamaño justo y la colocación en la pared solo mira esos.

Los dos son **prefiltros exactos**: solo descartan objetos que no pueden chocar, y la comprobación final
(`overlaps`) no cambia. Por eso las posiciones, los motivos de descarte y el presupuesto de trabajo son los
mismos que antes. Se comprobó contra la versión anterior en 1500 escenas aleatorias (23 783 comparaciones
idénticas) y, en las pruebas, contra un recorrido por fuerza bruta.

**Pruebas.** `test_fixed_array.py`: 29 casos, con 5 semillas × 5000 operaciones aleatorias contra una `list`
de referencia, más los casos límite. `test_arrangement_cells.py`: 10 casos de la integración.

### 2.7 `CircularDoublyLinkedList`: las paredes de `Room` como anillo

**Clase.** Un anillo doblemente enlazado: el último nodo enlaza con la cabeza y `head.prev` es el último.
"El siguiente del último" es el primero sin calcular ningún módulo.
- El índice 0 es la cabeza. Borrar el 0 pasa la cabeza al siguiente, como en una lista.
- El acceso por índice camina por el lado más corto: O(min(i, n−i)).
- `pairs()` devuelve las aristas consecutivas, incluida la de cierre.
- `rotate(k)` se comporta como `deque.rotate`.

**Dónde se usa.** `Room` (`app/geometry/room.py`) guarda sus vértices en el anillo, no en una `list`.
- La pared `i` va del vértice `i` al siguiente. El área, `contains_point` y `contains_rectangle` recorren las
  paredes con `pairs()`, sin `(i + 1) % n`.
- `walls()` da todas las paredes y `neighbor_walls(i)` la pared anterior y la siguiente (con `node.prev` y
  `node.next`, dando la vuelta en los extremos).
- Mover, insertar o borrar un vértice cambia el anillo en su sitio (valor del nodo, `insert_after`,
  `remove`). Si la forma nueva no es válida, se deshace el cambio en el anillo antes de lanzar el error, así
  que la sala queda igual.
- `Room` pone la regla del mínimo de 3 vértices. La API pública no cambió y la base de datos sigue guardando
  una lista JSON.

Los resultados son idénticos bit a bit a los de la versión con `list`: se comprobó en 3000 salas aleatorias
con ediciones (33 576 comparaciones), y la sala de 21 vértices (decisión 12) sigue pasando.

**Pruebas.** `test_circular_doubly_linked_list.py`: 39 casos contra `collections.deque`, con 5000 operaciones
aleatorias por semilla, más rotaciones, nodos ajenos, capacidad, uno y dos elementos. `test_room_ring.py`: 10
casos de la integración.

### 2.8 `NaryTree`: grupos y proyectos como árbol de dos niveles

**Clase.** Un árbol con raíz en el que cada nodo tiene cualquier número de hijos, en el orden en que se
añadieron. Cada nodo guarda su primer y su último hijo, y los hijos forman una lista doblemente enlazada de
hermanos (`prev_sibling`, `next_sibling`).
- `add_child` (al final) es O(1). `remove(nodo)` desengancha el subárbol en O(1) y suelta sus k nodos: O(k).
- `walk` recorre en profundidad en **preorden** (padre antes que sus hijos) con una `LinkedStack` propia en
  vez de recursión, así que un árbol muy profundo no agota la pila de Python. `count` y `depth_first` usan
  ese recorrido: O(k).

**Dónde se usa.** `GET /groups` (`app/routers/groups.py` y `app/services/project_tree.py`). La raíz es la
cuenta, sus hijos son los grupos (del más antiguo al más nuevo) y los hijos de cada grupo son sus proyectos.
Un solo recorrido en profundidad lista los grupos y cuenta los proyectos de cada uno. Sustituye al
`GROUP BY` de SQL y la respuesta es la misma: mismo orden, los grupos vacíos con 0 y los proyectos sin
grupo sin contar.

**Pruebas.** `test_nary_tree.py`: 20 casos, con 5 semillas × 3000 operaciones aleatorias contra un modelo de
referencia con `dict` y `list`, más los casos límite (capacidad, nodos ajenos, árbol de 20 000 niveles y de
5000 hijos). `test_project_tree.py`: 5 casos de la integración.

### 2.9 Cómo se demuestra

Desde la carpeta del backend:

```powershell
.venv\Scripts\python.exe -m pytest -q app/tests/test_linked_queue.py app/tests/test_doubly_linked_list.py app/tests/test_linked_stack.py app/tests/test_lru_cache.py app/tests/test_fixed_array.py app/tests/test_circular_doubly_linked_list.py app/tests/test_nary_tree.py
.venv\Scripts\python.exe -m pytest -q app/tests/test_rate_limit_queue.py app/tests/test_coherence_cache.py app/tests/test_room_undo.py app/tests/test_arrangement_cells.py app/tests/test_room_ring.py app/tests/test_project_tree.py
```

En la aplicación desplegada:
1. **Pila:** hacer dos fotos en un proyecto y pulsar "Deshacer foto" dos veces. Vuelve primero la sala de la
   segunda foto y después la del principio. Una tercera vez muestra el mensaje de "nada que deshacer".
2. **Cola:** hacer más de 10 peticiones a la IA en un minuto. La siguiente responde 429 y el frontend dice
   cuántos segundos esperar (`Retry-After`).
3. **LRU:** guardar dos veces un mueble con el mismo nombre en el catálogo. La segunda vez no llama al modelo;
   lo comprueba `test_coherence_cache.py`.

---

## 3. Seguridad

| Medida | Cómo | Dónde |
|---|---|---|
| Autenticación con JWT | HS256 firmado con `JWT_SECRET`; se rechaza cualquier otro `alg` (también `none`); caduca a los 60 min. En producción el servidor **no arranca** sin `JWT_SECRET` | `app/routers/auth.py`, `app/config.py` |
| Contraseñas | `scrypt` con sal aleatoria, ejecutado en un hilo aparte; nunca se guarda la contraseña en claro | `app/routers/auth.py` |
| Dueño con 404 | Toda sala se carga con `load_owned_room` (`id` **y** `user_id`). Una sala ajena responde **404**, igual que una inexistente, para no revelar que existe | `app/routers/rooms.py` |
| RLS | Row Level Security activado en todas las tablas, **sin políticas**: la API REST automática de Supabase queda cerrada para los roles `anon` y `authenticated`. El backend se conecta como dueño de las tablas y no se ve afectado | `schema.sql` (repo database); migración de arranque en `app/database/connection.py` |
| Límites de peticiones | Cuota de IA por cuenta (10/min, 200/día; la foto cuesta 3), fallos de login por email (10 en 15 min), registros (20/h en total y 5/h por IP). 429 con `Retry-After`. Un tope de catálogo (300, 409) y de grupos (50, 409) | `app/utils/rate_limit.py`, `app/config.py` |
| Fotos: 413 / 415 | Más de 5 MB → **413**, cortado por `BodySizeLimitMiddleware` antes de parsear y de autenticar, con o sin `Content-Length`. Solo JPEG o PNG, y la cabecera mágica debe coincidir con el tipo declarado → si no, **415**. Imagen no decodificable → 422 | `app/main.py`, `app/utils/middlewares.py`, `app/routers/spaces.py` |
| 422 sin eco | El manejador de `RequestValidationError` devuelve solo `type`, `loc` y `msg`, nunca `input` ni `ctx`. No refleja la petición y evita el 500 con bytes no UTF-8 | `app/main.py` |
| Errores sin datos internos | Un 500 da un mensaje genérico y un `request_id`; el detalle queda en el log del servidor. Un fallo de base de datos da 503 (conexión) o 500 (resto), con la clase del error limpia, sin SQL ni la URL | `app/main.py`, `app/routers/errors.py` |
| CORS por lista | `ALLOWED_ORIGINS` explícito, sin `*`, sin credenciales (la sesión va en la cabecera); `Retry-After` expuesto | `app/main.py` |
| Rutas de depuración | Desactivadas salvo `ENABLE_DEBUG_ROUTES` | `app/config.py`, `app/main.py` |
| IP del cliente | `X-Forwarded-For` solo se lee desde la derecha según `TRUSTED_PROXY_HOPS`; uvicorn arranca con `--no-proxy-headers` | `app/utils/rate_limit.py`, `Dockerfile`, `start.sh` |
| Secretos fuera de git | `.env*` ignorado en los tres repos (salvo `.env.example`, solo con nombres de variables). La clave de IA y la URL de la base viven solo en el panel de Render. Las pruebas usan SQLite en memoria y una guarda que impide conectar a una base remota | `.gitignore`, `app/tests/db_guard.py` |
| Datos no confiables en la IA | Los datos de la sala van como JSON entre `<datos_sala>` y `</datos_sala>`, con `<`, `>` y `&` escapados para que ningún nombre cierre el bloque; sin bucle de herramientas; máximo 20 mensajes de 2000 caracteres | `app/services/scene_assistant.py`, `app/schemas/chat.py` |
| Privacidad | El chat y la foto avisan de que el contenido se envía a un servicio de IA externo y la foto no se guarda. El log de auditoría solo registra método, ruta sin query string, código y tiempo: sin cuerpos, tokens ni mensajes | frontend (`src/i18n/es.ts`), `app/utils/middlewares.py` |

---

## 4. Decisiones de diseño relevantes para las estructuras

- **Sin `await` entre comprobar y registrar** en la cola del limitador y entre leer y quitar en la pila de
  deshacer. En asyncio, entre dos líneas sin `await` no se ejecuta otra corrutina, así que dos peticiones
  simultáneas no pueden colarse en el mismo hueco.
- **Copias, no operaciones inversas,** en la pila de deshacer: restaurar es asignar el estado guardado, sin
  calcular cómo revertir cada cambio.
- **La LRU no guarda errores:** un fallo pasajero de la IA no queda "recordado".
- **Todo acotado:** capacidad en cada estructura, `MAX_KEYS` en el limitador, 1000 entradas en la caché de
  coherencia y 200 salas × 5 copias en deshacer.

---

## 5. Cifras de las pruebas (`dev`; backend `9a9f09f` el 2026-11-08, frontend `b5bc525` el 2026-11-07)

| Qué | Resultado |
|---|---|
| Backend, suite completa (`pytest`, Python 3.14.7 local) | **893 passed** en 60,74 s |
| Backend, `ruff check` | All checks passed |
| Pruebas de las siete clases de `app/structures/` | **176**: cola 23, lista doble 25, pila 20, LRU 20, arreglo 29, anillo 39, árbol 20 |
| Pruebas de las integraciones | **78**: cola en el limitador 7, LRU de coherencia 12, Deshacer de la sala 34, arreglo en la colocación 10, anillo en `Room` 10, árbol en los grupos 5 |
| Operaciones aleatorias | 5000 por semilla (3000 en el árbol), comparadas tras cada paso con `deque`, `list`, `dict` u `OrderedDict` de referencia y con `check_invariants()` |
| Frontend (`vitest`) | **22 archivos, 219 passed** |

---

## 6. Qué no se probó

- **PostgreSQL real:** las pruebas del backend corren sobre SQLite en memoria. Las pruebas SQL del repo
  database (`tests/`, `migrations/`) no se ejecutaron contra un PostgreSQL real desde esta máquina.
- **Render real:** el reinicio que borra las pilas de deshacer y los contadores solo se simula
  (`store.clear()`). Tampoco se verificó que el proxy de Render añada la IP real al final de
  `X-Forwarded-For` (si no, solo se debilita el límite por IP).
- **IA real:** todas las pruebas usan un estimador y un cliente falsos; nunca se llamó al modelo desde las
  pruebas.
- **Navegador y móvil reales:** el botón "Deshacer foto", la cámara y el 3D solo se probaron con vitest. El AR
  no se probó en un Android.
- **Python 3.12:** producción usa la imagen `python:3.12-slim`; las pruebas locales corrieron con 3.14.7.
- **Varios workers o instancias:** el estado en memoria supone un único worker. Con varios, cada uno tendría
  sus propios límites y sus propias pilas.
- **Carga:** no hubo pruebas de rendimiento con muchos usuarios simultáneos.

---

## 7. Trabajo futuro

- **Acciones del chat** (`TAREA-CHAT-ACCIONES.md`): que el asistente proponga cambios en la sala (añadir,
  mover o quitar objetos) y el usuario los confirme, con su propio Deshacer.
- **Proyectos derivados en árbol** (`TAREA-RENOMBRAR.md`, sección 5): cada versión de un proyecto apunta a la
  anterior (`derivado_de`). Un árbol n-ario de versiones (se puede reutilizar `NaryTree`) con un resumen de
  cambios calculado sin IA y una vista de historial que recorra el árbol.
- **Celdas del suelo también en el layout** (`app/geometry/layout.py`): hoy `FloorCells` solo se usa al
  colocar un objeto (2.6), no en la búsqueda de huecos de `generate_layout`.
- **Persistir el historial de deshacer** en la base de datos para que sobreviva a un reinicio, y añadir
  Rehacer.
- **Traducir al español** los comentarios en inglés que quedan en `app/utils/rate_limit.py` (decisión 37), y
  el renombrado `OMNIROUTE_*` → `AI_*`.
