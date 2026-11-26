import { test, expect } from '@playwright/test';
import { validateErrorFeedProfile } from '../lib/error-feed-harness';

const owned = {
  project: 'futureagi-th8613-e2e', service: 'backend', apiUrl: 'http://localhost:28113',
  env: { ENV_TYPE: 'local', E2E_ERROR_FEED_PROFILE: 'th8613', PG_HOST: 'postgres',
    TEMPORAL_HOST: 'temporal:7233', ERROR_FEED_ESCALATION_MODE: 'disabled',
    ERROR_FEED_GROUPING_ENABLED: 'false' },
};

test('private Error Feed driver accepts only the dedicated local profile', () => {
  expect(() => validateErrorFeedProfile(owned)).not.toThrow();
});
for (const [name, change] of Object.entries({
  'development stack': { project: 'futureagi' },
  'remote API': { apiUrl: 'https://api.futureagi.com' },
  'foreign service': { service: 'worker' },
  'active background writes': { env: { ...owned.env, ERROR_FEED_ESCALATION_MODE: 'enabled' } },
  'external database': { env: { ...owned.env, PG_HOST: 'shared.example.com' } },
  'external Temporal': { env: { ...owned.env, TEMPORAL_HOST: 'shared.example.com:7233' } },
})) {
  test(`private Error Feed driver rejects ${name}`, () => {
    expect(() => validateErrorFeedProfile({ ...owned, ...change })).toThrow(/Error Feed profile/);
  });
}
