import { test, expect } from '../lib/fixtures';
import { seedHarnessEnvironment } from '../lib/simulate-seed';

// lib/simulate-seed.ts `environmentNeeds` writes what the agent needs from its environment into the authored
// contract. Prove what a flow relies on: the default environment version records it, both as authored and as the
// version runs with it (the list reads the latter when present), and a seed without the option records none.
test('a seeded environment records the environment needs it was given, and none when given none', async ({ actor, probe }, testInfo) => {
  test.setTimeout(120_000);
  const browser = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    environmentNeeds: ['browser'] });
  const plain = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({ browser, plain }) });

  const needs = async (environmentVersionId: string) => probe.pg<{ authored: unknown; runs_with: unknown }>(
    `SELECT documents->'contract'->'environment_needs' AS authored,
            COALESCE(documents->'run_contract', documents->'contract')->'environment_needs' AS runs_with
       FROM simulate_harness_environment_version WHERE id = $1`, [environmentVersionId]);
  expect(await needs(browser.environmentVersionId)).toEqual([{ authored: ['browser'], runs_with: ['browser'] }]);
  expect(await needs(plain.environmentVersionId)).toEqual([{ authored: null, runs_with: null }]);
});
