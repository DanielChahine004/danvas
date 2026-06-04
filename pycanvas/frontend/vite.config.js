import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build output goes to dist/, which is shipped with the Python package
// and served statically by FastAPI. base: './' keeps asset paths relative
// so the bundle works regardless of the mount path.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // Keep Monaco (large, lazy-loaded by the Repl) in its own chunk so it
        // doesn't bloat the initial app bundle and only downloads on demand.
        manualChunks(id) {
          if (id.includes('monaco-editor') || id.includes('@monaco-editor')) {
            return 'monaco'
          }
        },
      },
    },
  },
  server: {
    port: 5173,
  },
})
