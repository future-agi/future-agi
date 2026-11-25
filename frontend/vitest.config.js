import path from 'node:path';
import { BaseSequencer } from 'vitest/node';
import { defineConfig } from 'vitest/config';
import { recordPlan, selectedFiles } from './scripts/ci-vitest-shards.mjs';
import { mergeConfig } from 'vite';
import viteConfig from './vite.config.js';

// Only the manual benchmark sets a plan. Normal runs retain Vitest's default
// sequencing, process isolation, DOM environment and shared test setup.
class DurationSequencer extends BaseSequencer {
  async shard(specs) {
    const file = spec => path.relative(process.cwd(), spec.moduleId);
    const discovered = specs.map(file);
    const bins = recordPlan(discovered, process.env.CI_VITEST_HISTORY, process.env.CI_VITEST_PLAN);
    const wanted = selectedFiles(bins, this.ctx.config.shard.index, discovered);
    return specs.filter(spec => wanted.has(file(spec)));
  }
}

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      ...(process.env.CI_VITEST_PLAN && { sequence: { sequencer: DurationSequencer } }),
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
