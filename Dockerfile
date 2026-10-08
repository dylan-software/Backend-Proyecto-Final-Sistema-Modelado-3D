# Multi-stage build: keeps the final image small (no TypeScript compiler,
# no dev dependencies) and works unmodified on Render, Railway, Azure
# Container Apps/App Service, or AWS (ECS/App Runner/Elastic Beanstalk) —
# all of them can run "a Dockerfile that listens on $PORT".

FROM node:20-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY package*.json ./
RUN npm ci --omit=dev
COPY --from=build /app/dist ./dist
COPY src/db/schema.sql ./dist/db/schema.sql

# Most platforms inject PORT themselves; 4000 is just the local default.
EXPOSE 4000
CMD ["node", "dist/server.js"]
