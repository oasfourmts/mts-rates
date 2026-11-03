import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves the screens on :5173 and forwards /api to the FastAPI dev server on :8000.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
