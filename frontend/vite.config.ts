import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// 백엔드는 기본적으로 http://localhost:8000 에서 `/api` 접두사로 뜬다 (app.config.Settings.api_prefix).
// dev 서버는 같은 오리진으로 보이게 프록시로 넘긴다 — CORS 설정에 의존하지 않기 위해서다.
const BACKEND_ORIGIN = process.env.AILA_BACKEND_ORIGIN ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
      '/health': {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
