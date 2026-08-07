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
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
});
