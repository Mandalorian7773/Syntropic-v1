/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev proxy so the SPA always talks to same-origin /api, in dev and in the
// air-gapped demo alike. Targets come from env vars -- never hardcode a host.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: { outDir: 'dist', sourcemap: true },
  // `vite preview` serves the real production bundle. Same proxy as dev, so
  // the built artifact can be exercised exactly as it will run behind nginx.
  preview: {
    host: '0.0.0.0',
    port: 4173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    // The store test drives the real SSE client, which needs an absolute base.
    env: { VITE_API_BASE: 'http://127.0.0.1:8178' },
    testTimeout: 20000,
  },
});
