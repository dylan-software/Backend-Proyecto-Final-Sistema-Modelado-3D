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
| **httpx** | Cliente HTTP asíncrono hacia OmniRoute. |
| **pytest + aiosqlite** | Pruebas automatizadas con una base SQLite en memoria. |

Las operaciones pesadas de CPU (RANSAC) se ejecutan en un hilo con `asyncio.to_thread`, así el
servidor sigue atendiendo otras peticiones mientras calcula.

## Estructura

```
app/
├── main.py                 instancia FastAPI, CORS y creación de tablas al arrancar
├── database/connection.py  motor y fábrica de sesiones asíncronas
├── models/space.py         tablas habitaciones y muebles_sugeridos
├── schemas/space.py        esquemas Pydantic de entrada y salida
├── geometry/               octree, colisiones, filtro RANSAC y endpoints /geometry
├── services/omniroute.py   cliente de OmniRoute
├── routers/spaces.py       pipeline POST /api/v1/spaces/analyze
└── tests/                  pruebas unitarias y de integración
```

## Puesta en marcha

```
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --env-file .env
```

Documentación interactiva en <http://localhost:8000/docs>. Para las pruebas:
`pip install -r requirements-dev.txt` y luego `pytest app/tests`.

| Variable | Descripción |
|---|---|
| `DATABASE_URL` | Cadena de PostgreSQL. Acepta `postgresql://…`; el backend añade el driver asyncpg. |
| `OMNIROUTE_BASE_URL` | Por defecto `http://localhost:20128/v1`. |
| `OMNIROUTE_API_KEY` | Clave de OmniRoute. |
| `OMNIROUTE_MODEL` | Modelo o combo configurado en OmniRoute. |

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

Convención de coordenadas: metros, eje **y hacia arriba**. La habitación ocupa
`[0, ancho] × [0, alto] × [0, profundidad]`; el origen es la esquina mínima de las superficies
detectadas. En cada mueble, `posicion_x`/`posicion_z` son el centro de su base y `posicion_y` la
altura de esa base.
