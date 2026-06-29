import { defineConfig } from 'vite'

// The new danvas frontend. No React, no tldraw: panel content runs on Preact
// (aliased to `react`/`react-dom` so the Python-shipped React JSX compiles
// unchanged), the engine is framework-free TypeScript.
//
// base: './' keeps asset paths relative so the built bundle works from any mount
// path, exactly like the original — the Python server serves dist/ statically.
//
// The dev proxy points the in-browser app at a *running danvas Python server*
// (any `examples/*.py` calling canvas.serve(port=8000)). This is the test loop:
// the new frontend talks to the real backend over the real protocol, while the
// shipped danvas/frontend/dist is never touched.
const PY = 'http://localhost:8000'

export default defineConfig({
  base: './',
  esbuild: {
    jsx: 'automatic',
    jsxImportSource: 'preact',
  },
  resolve: {
    alias: {
      react: 'preact/compat',
      'react-dom/client': 'preact/compat/client',
      'react-dom': 'preact/compat',
      'react/jsx-runtime': 'preact/jsx-runtime',
      'react/jsx-dev-runtime': 'preact/jsx-dev-runtime',
    },
  },
  optimizeDeps: {
    include: ['preact', 'preact/compat', 'preact/hooks', 'preact/jsx-runtime'],
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/ws': { target: PY, ws: true, changeOrigin: true },
      // Every danvas HTTP side-route is /__name__ — proxy the whole family.
      '^/__.*': { target: PY, changeOrigin: true },
    },
  },
})
