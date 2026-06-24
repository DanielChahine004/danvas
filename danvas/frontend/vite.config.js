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
  },
  server: {
    port: 5173,
  },
})
