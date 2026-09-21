import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // Sets '/' for local dev ('npm run dev') and '/vivida_app/' only when building ('npm run build')
  base: process.env.NODE_ENV === 'production' ? '/vivida_app/' : '/',
  server: {
    port: 5173,
    open: true,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
}));