import { test, expect } from '../lib/fixtures';

test('probe reaches PG, CH and the API with actor scoping', async ({ actor, probe }) => {
  const pgRows = await probe.pg<{ email: string }>(
    'SELECT email FROM accounts_user WHERE email = $1', [actor.email]);
  expect(pgRows).toHaveLength(1);

  // A never-provisioned org id, not the actor's: the actor is worker-scoped and
  // shared with span-writing flows, so its span count is not deterministically 0.
  const chRows = await probe.ch<{ n: string }>('SELECT count() AS n FROM spans FINAL WHERE org_id = {org:String}',
    { org: crypto.randomUUID() });
  expect(Number(chRows[0].n)).toBe(0);   // proves the table, param binding and JSONEachRow round-trip

  const projects = await probe.apiList('/tracer/project/list_projects/',
    { project_type: 'observe', page_number: 0, page_size: 25 });
  expect(Array.isArray(projects)).toBe(true);
});
