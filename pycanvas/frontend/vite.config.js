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
    // Monaco is lazy-loaded (only when a Repl panel mounts), but vite by default
    // emits a <link rel="modulepreload"> for dynamic-import chunks — which would
    // eagerly fetch ~1 MB (gzipped) of editor on *every* startup, including the
    // common no-Repl canvas. Drop monaco from the preload list so it truly loads
    // on demand; all other chunks still preload to avoid request waterfalls.
    modulePreload: {
      resolveDependencies: (_file, deps) =>
        deps.filter((dep) => !dep.includes('monaco')),
    },
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
