import { defineConfig } from 'vitest/config';
import { mergeConfig } from 'vite';
import viteConfig from './vite.config.js';

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: ['./src/setupTests.js'],
      css: true,
      // Performance optimizations
      cache: true,
      maxConcurrency: 4,
      testTimeout: 30000,
      hookTimeout: 30000,
      // Coverage configuration
      coverage: {
        provider: 'v8',
        reporter: ['text', 'json', 'html', 'lcov'],
        exclude: [
          'node_modules/',
          'src/setupTests.js',
          'src/utils/test-utils.jsx',
          '**/*.test.{js,jsx,ts,tsx}',
          '**/*.stories.{js,jsx,ts,tsx}',
          'src/main.jsx',
          'dist/',
          'build/',
          '.storybook/',
        ],
        thresholds: {
          global: {
            branches: 70,
            functions: 70,
            lines: 70,
            statements: 70
          }
        }
      },
      // Include patterns for different test types
      include: [
        'src/**/*.{test,spec}.{js,jsx,ts,tsx}',
        'src/__tests__/**/*.{js,jsx,ts,tsx}',
        // api-journey UNIT tests only (*.test.*). The *.spec.js files under
        // api-journeys are Playwright specs (import @playwright/test) and must
        // NOT be collected by vitest.
        'scripts/api-journeys/**/*.test.{js,mjs}'
      ],
      // Exclude patterns
      exclude: [
        'node_modules/',
        'dist/',
        'build/',
        '.storybook/'
      ]
    }
  })
);
