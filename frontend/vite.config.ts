import react from '@vitejs/plugin-react';
import path from 'node:path';
import { createLogger } from 'vite';
// `vitest/config` re-exports Vite's defineConfig with the `test` block typed.
import { defineConfig } from 'vitest/config';

const API_TARGET = 'http://127.0.0.1:8000';

/**
 * Quieten the dev server when the backend simply isn't running.
 *
 * Working on the frontend alone is a normal thing to do, and the app is built to
 * survive it — the health dot reads "offline" and the story shell stays up. Vite's
 * default reaction is not so calm: every failed proxy request logs a red error
 * plus a Node stack trace, and the health query retries, so a single page load
 * buries the dev-server URL under a pile of tracebacks that read like the frontend
 * is broken.
 *
 * Filtering at the logger rather than replacing the proxy's error handler leaves
 * Vite in sole charge of the response, and degrades safely: if Vite ever changes
 * the message, the worst case is the old noise comes back.
 */
const logger = createLogger();
const logError = logger.error.bind(logger);
let outageReported = false;

logger.error = (msg, options) => {
  if (!msg.includes('http proxy error')) {
    logError(msg, options);
    return;
  }
  // One line per outage, not one per retry.
  if (!outageReported) {
    outageReported = true;
    logError(
      `  [api proxy] ${API_TARGET} is not reachable — start the backend with \`make run\`.\n` +
        '  Until then the health dot reads "offline"; the rest of the app works.',
      { ...options, error: null },
    );
  }
};

export default defineConfig({
  plugins: [react()],
  customLogger: logger,
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  server: {
    port: 5173,
    // Talk to Django on a same-origin path in dev so cookies and CSRF behave
    // the same way they will in production behind one hostname.
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        configure: (proxy) => {
          // Arm the notice again once the backend answers, so a restart mid-session
          // is reported rather than swallowed.
          proxy.on('proxyRes', () => {
            outageReported = false;
          });
        },
      },
      // Generated illustrations and audio, in development only. In staging and
      // production these are CDN URLs on another host and never touch Vite or
      // Django (PRODUCT.md §16.6).
      '/media': { target: API_TARGET, changeOrigin: true },

      // `/admax` — the ops dashboard, which Django serves, not React.
      //
      // Without this line Vite's SPA fallback answers `/admax` with `index.html`,
      // React Router matches nothing, and the browser renders its own "404 Not
      // Found". That reads as a missing route when the route is fine and simply
      // lives on the other server — in production one hostname fronts both, so
      // this proxy is what makes dev behave the way the deployed box does.
      //
      // This is the dev-side counterpart of `infra/admax-setup.sh`, which fixes
      // the identical bug at the Caddy edge: any edge that fronts both the PWA
      // and Django has to route `/admax` explicitly, or the SPA catch-all eats
      // it and React Router answers 404. Change one, consider the other.
      //
      // `changeOrigin: false`, unlike `/api` above, and the difference is load
      // bearing: `/admax` signs in with a session + CSRF form POST, so Django
      // compares the browser's `Origin` (`http://localhost:5173`) against the
      // `Host` it is handed. Rewriting Host to `127.0.0.1:8000` makes those two
      // disagree and every sign-in 403s. The parent API is bearer-token and
      // exempt from that check, so it is free to rewrite.
      '/admax': { target: API_TARGET, changeOrigin: false },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
});
