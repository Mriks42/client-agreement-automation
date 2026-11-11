import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In dev, Vite serves the SPA on :5173 and proxies API calls to FastAPI on :8000, so the browser
// sees one origin exactly like production (where FastAPI serves the built SPA itself).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/healthz': 'http://localhost:8000',
    },
  },
  build: {
    sourcemap: true,
  },
})
