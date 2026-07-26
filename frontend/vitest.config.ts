import { defineConfig, mergeConfig } from 'vitest/config'

import viteConfig from './vite.config.ts'

/**
 * The test runner, built on the app's own Vite config rather than beside it.
 *
 * `mergeConfig` and not a second `defineConfig` from scratch: the `@/` alias is already declared
 * twice — once for the bundler, once for `tsc -b` — and a third private copy here is how a test
 * suite ends up resolving `@/components/kind-node` to a file the app does not ship. Tests import
 * exactly what the browser imports.
 *
 * `jsdom` because everything worth testing in these components is a DOM fact: what a screen
 * reader would be told the disc is, what URL the photograph is fetched from, and what is left on
 * screen when that fetch fails. None of those can be asserted against a rendered string.
 */
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      include: ['src/**/*.test.tsx'],
    },
  }),
)
