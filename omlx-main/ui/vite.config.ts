import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev server proxies `/ui/api` to the local FastAPI backend.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/ui/api': {
        target: process.env.OMLX_API_ORIGIN ?? 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
