import type { TestType } from '@playwright/test';
import { createMoneyProvider, type MoneyProvider, type ProviderOptions } from './provider';

/** Consumer module for the E6 named-fixtures slot requested by the SHA-verified
 * CONSUMER-ABI-REQUEST.json (b78a171f...803 first verified before authoring).
 * The subsequently published CONSUMER-ABI.json is state=changes_required and
 * marks E6 orderedInvocations/fixtures implemented=false (also E0-E4/appSide).
 * This is an extension implementation for review, NOT a claim the runner calls
 * it. The current ABI exposes no executable fixture-registration signature.
 * The actual runner must bind sourceRef, exact module/export/options, the worker
 * PID's inherited guard and the same-job gateway/fixture network namespace.
 * Tests importing this module have NO startup side effects. Only the accepted
 * TestType's requested moneyProvider fixture creates a loopback listener.
 * No actor dependency: no org/system key, signup or credential auto-provisioning.
 */
export function withNamedFixtures<T extends {}, W extends {}>(
  acceptedTest: TestType<T, W>, options: ProviderOptions,
): TestType<T & { moneyProvider: MoneyProvider }, W> {
  if (!options || !Number.isSafeInteger(options.barrierTimeoutMs)) throw new Error('MONEY_FIXTURE_OPTIONS_REQUIRED');
  return acceptedTest.extend<{ moneyProvider: MoneyProvider }>({
    moneyProvider: async ({}, use, info) => {
      const service = await createMoneyProvider(options);
      try {
        await info.attach('money-fixture-lifecycle', { contentType: 'application/json',
          body: JSON.stringify({ schema: 'money-fixture-lifecycle/v1', status: 'started',
            pid: process.pid, address: '127.0.0.1', port: Number(new URL(service.origin).port),
            productRowCoverage: false }) });
        await use(service);
        // No positive can leave unused attempts, unreleased barriers, extra
        // calls or a fixture timeout behind and qualify by HTTP status alone.
        service.verifyComplete();
      } finally {
        await service.close();
        await info.attach('money-fixture-lifecycle', { contentType: 'application/json',
          body: JSON.stringify({ schema: 'money-fixture-lifecycle/v1', status: 'closed',
            pid: process.pid, ...service.health(), productRowCoverage: false }) });
      }
    },
  });
}

// These are requests to the runtime OWNER, not invented CLI flags or a dispatcher.
export const MONEY_RUNNER_REQUESTS = [
  'Consume this module in E6 fixtures with explicit fixture dead-man options; fail a declared but unconsumed fixture slot.',
  'Bind listener to the same job and gateway network namespace, retain loopback-only listening and the existing egress/18d guards. Distributed needs an accepted projection, not 0.0.0.0.',
  'Admit per-scenario synthetic native provider base URLs into the local profile with exact config/SSRF provenance. No public hosts, provider TLS bypass or production fallback.',
  'Discover fixture qualification specs separately from J11/J12 full-row inventory; reject skipped/zero/extra cases. Retain every accepted reporter and per-PID observer.',
  'Execute native stream-client qualification including cancel-before/after-usage and write-barrier controls on actual runtime; writer callbacks alone are not client delivery.',
  'Join F02/F05 real Redis/private-ledger and worker event barriers for late usage/replay; duplicate provider frames are not worker replay.',
] as const;
