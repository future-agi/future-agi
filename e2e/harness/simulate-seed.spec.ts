import { test, expect } from '@playwright/test';
import { resolveBackendContainer } from '../lib/simulate-seed';

// Only the Docker listing boundary is replaced. The same parser and selector
// used by OBS-E2E-034 runs without a browser, stack, or shared process env.
function row(name: string, project: string, service: string, port?: number): string {
  return [name, port ? `127.0.0.1:${port}->5432/tcp` : '', project, service].join('\t');
}

function resolve(rows: string[], port = 25432, override = ''): string {
  return resolveBackendContainer(`postgresql://localhost:${port}/test_e2e`, override,
    () => rows.join('\n'));
}

for (const service of ['backend', 'app']) {
  test(`simulation seeding finds the ${service} runtime by compose labels, not its name`, () => {
    expect(resolve([
      row('custom-database-name', 'selected', 'postgres', 25432),
      row('custom-runtime-name', 'selected', service),
      row('selected-worker-1', 'selected', 'worker'),
    ])).toBe('custom-runtime-name');
  });
}

for (const [port, expected] of [[25432, 'distributed-api'], [35432, 'standalone-app']] as const) {
  test(`simulation seeding selects only the stack publishing Postgres port ${port}`, () => {
    expect(resolve([
      row('distributed-pg', 'distributed', 'postgres', 25432),
      row('distributed-api', 'distributed', 'backend'),
      row('standalone-pg', 'standalone', 'postgres', 35432),
      row('standalone-app', 'standalone', 'app'),
    ], port)).toBe(expected);
  });
}

test('simulation seeding refuses a runtime in another project even if its name looks right', () => {
  expect(() => resolve([
    row('selected-postgres-1', 'selected', 'postgres', 25432),
    row('selected-backend-1', 'other-project', 'backend'),
    row('selected-app-1', 'selected-prefix-neighbour', 'app'),
  ])).toThrow('expected exactly one running backend/app service, found 0');
});

test('simulation seeding refuses when the selected stack has no Django runtime', () => {
  expect(() => resolve([
    row('selected-postgres-1', 'selected', 'postgres', 25432),
    row('selected-worker-1', 'selected', 'worker'),
  ])).toThrow('expected exactly one running backend/app service, found 0');
});

for (const services of [['backend', 'app'], ['backend', 'backend']]) {
  test(`simulation seeding refuses ambiguous runtimes (${services.join(', ')})`, () => {
    expect(() => resolve([
      row('selected-postgres-1', 'selected', 'postgres', 25432),
      ...services.map((service, index) => row(`runtime-${index}`, 'selected', service)),
    ])).toThrow('expected exactly one running backend/app service, found 2');
  });
}

test('simulation seeding refuses an absent Postgres without falling back to another stack', () => {
  expect(() => resolve([
    row('other-postgres-1', 'other', 'postgres', 35432),
    row('other-backend-1', 'other', 'backend'),
  ])).toThrow('expected exactly one running compose Postgres on port 25432');
});

test('simulation seeding refuses ambiguous Postgres owners', () => {
  expect(() => resolve([
    row('first-postgres-1', 'first', 'postgres', 25432),
    row('second-postgres-1', 'second', 'postgres', 25432),
    row('first-backend-1', 'first', 'backend'),
  ])).toThrow('expected exactly one running compose Postgres on port 25432');
});

test('simulation seeding refuses a Postgres without a compose project label', () => {
  expect(() => resolve([
    row('looks-like-a-project-postgres-1', '', 'postgres', 25432),
    row('looks-like-a-project-backend-1', 'looks-like-a-project', 'backend'),
  ])).toThrow('with a project label');
});

test('simulation seeding keeps the explicit override without discovering or guessing a stack', () => {
  const container = resolveBackendContainer('not-a-url', 'explicit-runtime', () => {
    throw new Error('Docker discovery must not run for an explicit override');
  });
  expect(container).toBe('explicit-runtime');
});
