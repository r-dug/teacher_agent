import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import basicSsl from '@vitejs/plugin-basic-ssl'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss(), basicSsl()],
  optimizeDeps: {
    // Both loaded as UMD globals via <script> tags in index.html — not imported through Vite.
    exclude: ['@ricky0123/vad-web', 'onnxruntime-web'],
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, './src'),
    },
  },
  server: {
    host: true,  // bind to 0.0.0.0 so LAN devices can reach the dev server
    proxy: {
      '/api': {
        target: 'https://localhost:8000',
        changeOrigin: true,
        secure: false,
      },
      '/ws': {
        target: 'wss://localhost:8000',
        changeOrigin: true,
        ws: true,
        secure: false,
      },
    },
  },
  build: {
    outDir: '../frontend/static',
    emptyOutDir: true,
  },
  worker: {
    format: 'es',
  },
})
