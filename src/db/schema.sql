-- Full schema. Works on Supabase (postgis + vector are both available
-- as managed extensions there) and on the local Postgres via docker-compose.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS postgis;

-- Catalog of furniture/objects. Every object created manually by the
-- user goes through here too — `name` is required.
CREATE TABLE IF NOT EXISTS furniture (
  id                 TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  width_cm           NUMERIC NOT NULL CHECK (width_cm > 0),
  height_cm          NUMERIC NOT NULL CHECK (height_cm > 0),
  depth_cm           NUMERIC NOT NULL CHECK (depth_cm > 0),
  category           TEXT NOT NULL,
  material           TEXT NOT NULL,
  price              NUMERIC NOT NULL CHECK (price >= 0),
  style              TEXT NOT NULL,
  style_embedding    VECTOR(512), -- CLIP ViT-B/32
  created_manually   BOOLEAN NOT NULL DEFAULT false,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HNSW: better recall/speed, does not require existing data beforehand.
CREATE INDEX IF NOT EXISTS idx_furniture_style_embedding
  ON furniture USING hnsw (style_embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- Projects: one space per user.
CREATE TABLE IF NOT EXISTS projects (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  name        TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Shape of the scanned space: IRREGULAR POLYGON in meters (planar).
-- Important: no geographic SRID (4326 = GPS lat/lon) here, because this
-- is NOT real-world geography — it's local meters relative to the AR
-- scan origin. Using 4326 would silently break any real distance/area
-- calculation (a bug in an earlier version of this schema).
--
-- The shape is editable: the user can update it after the initial scan
-- (move/add/remove vertices), so `shape`/`height_m` are simply
-- overwritten on every edit — `updated_at` tracks the last change.
CREATE TABLE IF NOT EXISTS spaces (
  project_id  TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  shape       geometry(POLYGON) NOT NULL,
  height_m    NUMERIC NOT NULL CHECK (height_m > 0),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_spaces_shape
  ON spaces USING GIST (shape);

-- Objects placed inside a space (the final layout). This is the table
-- the AI chat panel queries to know "what objects does this space have".
CREATE TABLE IF NOT EXISTS placed_items (
  id          BIGSERIAL PRIMARY KEY,
  project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  furniture_id TEXT NOT NULL REFERENCES furniture(id),
  x           NUMERIC NOT NULL,
  y           NUMERIC NOT NULL,
  rotation    NUMERIC NOT NULL DEFAULT 0,
  added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_placed_items_project
  ON placed_items (project_id);

-- ---------------------------------------------------------------------
-- Security (Supabase): every table in the `public` schema is exposed by
-- Supabase's auto-generated REST API unless Row Level Security is on.
-- This app never uses that REST API — only the backend talks to the
-- database, through a direct Postgres connection as the table owner,
-- and table owners bypass RLS. Enabling RLS with NO policies therefore
-- locks the REST API down completely while leaving the backend
-- unaffected. Harmless on a plain local Postgres.
-- ---------------------------------------------------------------------
ALTER TABLE furniture    ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects     ENABLE ROW LEVEL SECURITY;
ALTER TABLE spaces       ENABLE ROW LEVEL SECURITY;
ALTER TABLE placed_items ENABLE ROW LEVEL SECURITY;
