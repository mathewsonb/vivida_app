import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // Sets '/' for local dev ('npm run dev') and '/vivida/' only when building ('npm run build')
  base: command === 'serve' ? '/' : '/vivida/',
  server: {
    port: 5173,
    open: true,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
}));