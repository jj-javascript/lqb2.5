import { defineConfig } from '@playwright/test';

/**
 * Dual-target E2E config.
 *
 * Local mode (default): the webServer entry point injects fixture books
 * into _books/, builds the site with a test config override into
 * _site_test/, and serves it on http://127.0.0.1:4173. (Playwright starts
 * the webServer before global setup, so the build lives in the server
 * entry point; fixture cleanup happens in globalTeardown.)
 *
 * Remote mode: set E2E_BASE_URL (e.g. npm run test:e2e:remote) to run the
 * content-agnostic specs against a deployed site such as
 * https://www.lqb2.co/libraryviz/. Fixture injection, the Jekyll build, and
 * the web server are skipped, and @fixtures-tagged tests are excluded.
 */
const REMOTE = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  ...(REMOTE
    ? { grepInvert: /@fixtures/ }
    : {
        globalTeardown: './tests/e2e/global-teardown.ts',
        webServer: {
          command: 'node tests/e2e/start-server.mjs',
          url: 'http://127.0.0.1:4173/',
          // Never reuse a running server: it would serve a stale build and
          // the tests would silently validate old code.
          reuseExistingServer: false,
          timeout: 180_000, // includes the Jekyll build
        },
      }),
  use: {
    baseURL: REMOTE || 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
