import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

/**
 * `@/` resolves to `src/`, and it has to be declared twice — here for the bundler and in
 * `tsconfig.app.json` for the type checker. They are two separate resolvers and neither reads the
 * other; a path that resolves in `vite build` but fails `tsc -b` is what a missing half looks like.
 *
 * The alias exists because the timeline was ported from a Next.js app whose every import is
 * `@/components/…`. Keeping the specifiers identical is what lets the port stay diffable against
 * its source, which is the only cheap way to tell a faithful port from a rewrite.
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
