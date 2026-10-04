import "dotenv/config";
import express from "express";
import cors from "cors";
import { router as furnitureRouter } from "./routes/furniture";
import { router as spacesRouter } from "./routes/spaces";
import { router as layoutRouter } from "./routes/layout";
import { router as chatRouter } from "./routes/chat";

export function createApp() {
  const app = express();
  app.use(express.json({ limit: "5mb" }));

  // CORS: lets the frontend (on Vercel, a different domain) talk to
  // this backend (on Render/Railway/Azure/AWS, yet another domain).
  // FRONTEND_URL can be a comma-separated list (handy for having both
  // the Vercel preview URL and the production domain at once).
  const allowedOrigins = (process.env.FRONTEND_URL ?? "http://localhost:3000").split(",").map((s) => s.trim());

  app.use(
    cors({
      origin: (origin, callback) => {
        if (!origin || allowedOrigins.includes(origin)) {
          callback(null, true);
        } else {
          callback(new Error(`Origin not allowed by CORS: ${origin}`));
        }
      },
    })
  );

  app.get("/api/health", (_req, res) => {
    res.status(200).json({ ok: true, service: "backend-3d-project" });
  });

  app.use("/api/furniture", furnitureRouter);
  app.use("/api/spaces", spacesRouter);
  app.use("/api/layout", layoutRouter);
  app.use("/api/chat", chatRouter);

  // Centralized error handler — never lets a raw stack trace leak out.
  app.use((err: Error, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    res.status(500).json({ error: err.message });
  });

  return app;
}

if (require.main === module) {
  const app = createApp();
  const port = Number(process.env.PORT ?? 4000);
  app.listen(port, () => {
    console.log(`Backend listening on http://localhost:${port}`);
  });
}
