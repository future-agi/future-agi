// Defaults target the managed futureagi-e2e stack (bin/e2e). Attach mode
// overrides all of these via E2E_* env vars — including E2E_CH_URL/E2E_PG_URL,
// which storage-lane assertions need to reach the attached stack's stores.
export const E2E = {
  appUrl: process.env.E2E_APP_URL ?? 'http://localhost:3100',
  apiUrl: process.env.E2E_API_URL ?? 'http://localhost:8100',
  collectorUrl: process.env.E2E_COLLECTOR_URL ?? 'http://localhost:24318',
  gatewayUrl: process.env.E2E_GATEWAY_URL ?? 'http://localhost:28090',
  chUrl: process.env.E2E_CH_URL ?? 'http://localhost:28123',
  chDatabase: process.env.E2E_CH_DB ?? 'default',
  pgUrl: process.env.E2E_PG_URL ?? 'postgresql://futureagi:futureagi@localhost:25432/futureagi',
} as const;
