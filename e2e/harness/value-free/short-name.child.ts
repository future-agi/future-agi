// HR-JRN-01 child (child.config.ts; spawned by harness/value-free-live.spec.ts with
// E2E_VALUE_FREE=1). A workspace whose name is too short to substitute safely: the test
// publishes the name first (a plain attach, a log line, a loose file), as a careless test
// would, and never registers it. The fixture's own Postgres read at teardown finds it and
// refuses the run, which must then publish nothing at all. lib/scope-actors (not the walk
// fixture), so no browser starts.
import { writeFileSync } from 'node:fs';
import { test, expect } from '../../lib/scope-actors';

const probes = JSON.parse(process.env.E2E_VALUE_FREE_PROBES ?? 'null') as { shortName: string };
if (!probes) throw new Error('E2E_VALUE_FREE_PROBES is set by harness/value-free-live.spec.ts');

test.use({ trace: 'off', screenshot: 'off', video: 'off' });

test('value-free child: a workspace name too short to hide refuses the run', async ({ scopeActors: scopes }, testInfo) => {
  const made = await scopes.send<{ result: { workspace: { id: string; name: string } } }>(scopes.ownerA, 'POST',
    '/accounts/workspaces/', { name: probes.shortName });
  expect(made.status).toBe(201);
  expect(made.body.result.workspace.name).toBe(probes.shortName);
  await testInfo.attach('vf-short', { contentType: 'text/plain', body: `workspace ${made.body.result.workspace.name}` });
  console.log(`vf-short ${made.body.result.workspace.name}`);
  writeFileSync(testInfo.outputPath('short-loose.txt'), made.body.result.workspace.name);
});
