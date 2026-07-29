import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/__tests__/setup.ts'],
    globals: true,
    // Run test files sequentially. Files still get isolated environments; this just stops
    // 30 files from running in parallel and oversubscribing CI's 2 cores, which starved the
    // React effect/async chains and made findBy assertions flake (different tests each run).
    fileParallelism: false,
    // Headroom over the 5s default so a slow-CI findBy (asyncUtilTimeout 5s) can't hit the limit.
    testTimeout: 15000,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
    },
  },
});
