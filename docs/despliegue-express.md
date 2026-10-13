# Backend — Render / Railway / Azure / AWS

Express + TypeScript API. Ships with a `Dockerfile`, which is what makes it portable: every one of those platforms
can run "a Dockerfile that listens on `$PORT`" and nothing else in the code changes between them.

## Environment variables (set these on the platform, not in Git)
| Variable | Required | Value |
|---|---|---|
| `DATABASE_URL` | yes | Supabase **Session pooler** string (see `proyecto-3d-database/README.md`) |
| `FRONTEND_URL` | yes | Your Vercel URL(s), comma-separated. Without it the browser blocks all requests (CORS). |
| `PORT` | no | Injected by the platform |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | for the AI chat | Without the key the chat returns a clear error; everything else works |
| `S3_*`, `AI_*` | no | Optional; features degrade gracefully when unset |

## Render
Render dashboard → **New → Web Service** → connect the repo → Runtime **Docker**. Or **New → Blueprint** to use the
included `render.yaml`. Health check path: `/api/health`. Then fill in the variables above.
Note: free plans on several platforms put the service to sleep when idle, so the first request can be slow.

## Railway / Azure / AWS
Point the platform's "deploy from Dockerfile" option at the `Dockerfile` in this folder and set the same variables:
Railway → New Project → Deploy from repo; Azure → Container Apps / App Service (container); AWS → App Runner / ECS / Elastic Beanstalk.
Container port is `4000` unless the platform injects `PORT` (the server reads it).

## Run locally
```
cp .env.example .env     # defaults match the local docker-compose database
npm install
npm run dev              # http://localhost:4000   (GET /api/health)
```
Other scripts: `npm test`, `npm run typecheck`, `npm run build` (emits `dist/`), `npm start`.

## Note on `src/db/schema.sql`
It is the same file as `proyecto-3d-database/schema.sql`. It stays here only because the Dockerfile copies it into
the image for reference. The database itself is created by running that SQL once in Supabase — the backend does not
run migrations on startup.

## API
`GET /api/health` · `GET|POST /api/furniture` · `GET /api/furniture/similar` · `POST|GET /api/spaces/:projectId` ·
`PATCH /api/spaces/:projectId/vertices/:index` · `GET /api/spaces/:projectId/items` · `POST /api/layout/generate` · `POST /api/chat`
