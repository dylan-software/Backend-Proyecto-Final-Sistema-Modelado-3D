# Seguridad de las contraseñas

Este documento explica cómo protege el proyecto la contraseña de cada cuenta en sus dos momentos:
**en tránsito** (cuando viaja del navegador al servidor) y **en reposo** (cuando queda guardada en la
base de datos). Cada afirmación indica el archivo donde se puede comprobar.

Arquitectura: navegador → frontend en Vercel (Next.js) → backend en Render (FastAPI) → PostgreSQL en
Supabase.

## Resumen

| Aspecto | Qué se hace | Dónde |
|---|---|---|
| Algoritmo del hash | scrypt (función de derivación de claves «memory-hard») | `app/routers/auth.py` (`hash_password`) |
| Parámetros | N = 2^14 (16 384), r = 8, p = 1; salida de 64 bytes | `app/routers/auth.py` (`SCRYPT_N`, `SCRYPT_R`, `SCRYPT_P`) |
| Sal | 16 bytes aleatorios por contraseña, con `secrets.token_bytes` | `app/routers/auth.py` |
| Formato guardado | `scrypt$<sal en hex>$<hash en hex>` en `usuarios.password_hash` | `app/models/user.py` |
| Comparación | En tiempo constante (`hmac.compare_digest`) | `app/routers/auth.py` (`verify_password`) |
| Email inexistente | Se calcula igualmente un scrypt contra un hash falso: tarda lo mismo y da el mismo mensaje | `app/routers/auth.py` (`_DUMMY_HASH`) |
| Longitud | Registro: 8 a 128 caracteres. Login: 1 a 128 | `app/schemas/auth.py` |
| Fuerza bruta | 10 fallos por email cada 15 minutos; después, 429. Registros limitados por hora y por IP | `app/utils/rate_limit.py` |
| Envío desde el frontend | `POST` con cuerpo JSON (nunca en la URL), a la URL de `NEXT_PUBLIC_API_URL` | `src/lib/api.ts`, `src/components/AuthScreen.tsx` |
| HTTPS | Render y Vercel sirven por HTTPS y redirigen HTTP a HTTPS en su proxy | Configuración de cada plataforma |
| HSTS | `Strict-Transport-Security: max-age=31536000; includeSubDomains` en todas las respuestas del backend | `app/utils/middlewares.py`, `app/main.py` |
| Logs | No se registra el cuerpo de ninguna petición; el 422 no repite lo enviado | `app/utils/middlewares.py`, `app/main.py` |

## 1. En tránsito

### 1.1 Del navegador al backend

1. El formulario de acceso usa `type="password"` y `autocomplete="current-password"` o
   `"new-password"`, para que el gestor de contraseñas del navegador la reconozca.
2. Al enviar, el frontend llama a `api.login` o `api.register`. Ambas hacen un `fetch` con método
   `POST` y la contraseña **en el cuerpo JSON**, nunca en la URL. Las URL sí suelen quedar en
   historiales, registros de proxies y la cabecera `Referer`; el cuerpo no.
3. La dirección del backend sale de `NEXT_PUBLIC_API_URL`. En producción es la URL `https://` de Render.
   Además, una página servida por HTTPS (Vercel) no puede llamar a una API por HTTP: el navegador la
   bloquea como «contenido mixto». Así que, desplegada, la contraseña solo puede viajar cifrada con TLS.
4. TLS lo termina el proxy de Render, que además redirige cualquier petición `http://` a `https://`. La
   aplicación no redirige por su cuenta: uvicorn arranca con `--no-proxy-headers` (para que nadie
   falsifique la IP con `X-Forwarded-For`), así que desde dentro todas las peticiones parecen HTTP y una
   redirección propia entraría en un bucle.

### 1.2 HSTS

Una redirección de HTTP a HTTPS deja un hueco: la **primera** petición sale sin cifrar y un atacante en la
misma red (por ejemplo, una wifi pública) podría interceptarla. HSTS lo cierra. Con la cabecera

```
Strict-Transport-Security: max-age=31536000; includeSubDomains
```

el navegador recuerda durante un año que ese dominio solo se usa por HTTPS. A partir de entonces convierte
él mismo cualquier `http://` en `https://` antes de enviar nada, y no deja saltarse un certificado
inválido.

- La añade `StrictTransportSecurityMiddleware`, el middleware más externo, así que llega en todas las
  respuestas: las correctas, los errores 401, 413, 422 y 429 y las preflight de CORS. El 500 genérico se
  construye fuera de la pila de middlewares y la añade él mismo.
- Los navegadores solo hacen caso de HSTS cuando llega por HTTPS. En local (`http://localhost`) se ignora
  y no molesta.
- No lleva `preload`: esa lista se pide como dueño del dominio, y `onrender.com` no es nuestro.
- Pruebas: `app/tests/test_security_headers.py` (8 pruebas).

### 1.3 Después del login

El backend no devuelve la contraseña ni su hash: responde con un token de sesión firmado con HS256 que
caduca en 60 minutos (`JWT_EXPIRE_MINUTES`). El frontend guarda **solo ese token** y lo envía en la
cabecera `Authorization: Bearer`, no en cookies. Por eso CORS va con `allow_credentials=False` y solo
admite los orígenes de `ALLOWED_ORIGINS`. La contraseña no se guarda en el navegador: ni en
`localStorage`, ni en `sessionStorage`, ni en la consola. La lista de «cuentas usadas en este navegador»
guarda solo correos.

## 2. En reposo

### 2.1 Por qué un hash y por qué scrypt

La base de datos **nunca** guarda la contraseña, ni cifrada ni en claro: guarda un *hash*, que no se
puede deshacer. Para comprobar un login se vuelve a calcular el hash con la contraseña recibida y se
compara con el guardado.

No sirve cualquier hash. SHA-256 es muy rápido: una tarjeta gráfica prueba miles de millones de
contraseñas por segundo. scrypt está diseñado para ser lento y para gastar memoria:

- Con N = 2^14 y r = 8, cada cálculo usa unos 16 MiB de memoria (128 · N · r bytes). Eso encarece mucho
  probar contraseñas en paralelo con GPU o con hardware específico.
- Viene en la biblioteca estándar de Python (`hashlib.scrypt`): no añade dependencias.
- En el servidor tarda del orden de decenas de milisegundos por login. Se ejecuta en un hilo aparte
  (`asyncio.to_thread`) para no bloquear el resto de peticiones.

### 2.2 La sal

Cada contraseña lleva su propia sal de 16 bytes aleatorios, generada con `secrets` (fuente criptográfica
del sistema operativo). Por eso:

- Dos personas con la misma contraseña tienen hashes distintos.
- No sirven las tablas precalculadas («rainbow tables»): habría que atacar cada cuenta por separado.

La sal no es secreta: se guarda junto al hash, en `scrypt$<sal>$<hash>`.

### 2.3 Comparación y mensajes

- `hmac.compare_digest` compara en tiempo constante: medir cuánto tarda la respuesta no revela cuántos
  bytes coincidían.
- Si el email no existe, se calcula igualmente un scrypt contra un hash de relleno. El tiempo de respuesta
  no delata qué correos están registrados, y el mensaje es el mismo en los dos casos («Email o contraseña
  incorrectos.»).
- Un hash con un formato distinto de `scrypt$...` se rechaza, sin lanzar errores.

### 2.4 Límite de intentos

Tras 10 fallos de un mismo email en 15 minutos, ese email recibe un 429 sin que se compruebe la
contraseña, aunque sea la correcta. El límite va por email y no por IP, para que cambiar de IP o
falsificar `X-Forwarded-For` no sirva para saltárselo. Contrapartida aceptada: cualquiera puede bloquear
un email durante la ventana. El registro también tiene límite: 20 por hora en total y 5 por hora por IP.

## 3. Lo que nunca aparece en los registros

- El middleware de auditoría registra solo el método, la ruta sin la query, el estado y la duración. Nunca
  el cuerpo (`app/utils/middlewares.py`).
- El 422 por datos inválidos devuelve solo `type`, `loc` y `msg` de cada error. Se quitan `input` y `ctx`,
  que repetirían lo enviado, contraseña incluida (`app/main.py`; prueba en
  `app/tests/test_validation_errors.py`).
- Los errores de base de datos se registran sin la SQL ni sus parámetros, y con cualquier `password=...`
  enmascarado (`app/routers/errors.py`).
- Un error no previsto devuelve al cliente un mensaje genérico y un `request_id`. El detalle queda solo en
  el log del servidor.
- Las peticiones salientes de `httpx` no se registran en nivel INFO.

## 4. Limitaciones conocidas y mejoras posibles

- **Parámetros de scrypt.** N = 2^14 es el valor interactivo clásico del artículo original de scrypt. Las
  guías actuales (OWASP) recomiendan subirlo (por ejemplo, N = 2^17). Hacerlo exigiría guardar los
  parámetros dentro del hash y volver a calcular el hash de cada cuenta en su siguiente login. Además,
  cada login usaría más memoria en un servidor pequeño.
- **Sin «pepper»**, es decir, sin un secreto del servidor mezclado en el hash.
- **Token en `localStorage`.** Si la página sufriera un XSS, el script podría leer el token, aunque nunca
  la contraseña. Una cookie `HttpOnly` lo evitaría, pero cambiaría CORS y la autenticación.
- **Fuerza de la contraseña.** Solo se exige la longitud (8 a 128); no se comprueba contra listas de
  contraseñas filtradas.
- **Redirección y HSTS del frontend.** Dependen de Vercel. HSTS del backend se añadió en este proyecto; la
  redirección del backend depende de Render.

## 5. Cómo comprobarlo en producción

```
curl.exe -sI https://<backend>.onrender.com/          # debe incluir strict-transport-security
curl.exe -sI http://<backend>.onrender.com/           # debe redirigir (301/308) a https://
curl.exe -sI https://<frontend>.vercel.app/           # cabeceras del frontend (HSTS de Vercel)
```

En las herramientas de desarrollo del navegador (pestaña Red), el login es un `POST` a
`.../api/v1/auth/login`: la URL no contiene la contraseña y el protocolo es `h2` o `http/1.1` sobre TLS.
