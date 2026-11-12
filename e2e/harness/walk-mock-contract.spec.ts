import { test, expect } from '@playwright/test';
import { MockUpstream } from '../lib/upstream';
import { parallelCalls } from '../lib/parallel-caller';
import { withWalkMocks, WALK_COMMANDS, WALK_PORTS, WALK_MOCKS } from '../lib/walk-mock-contract';
import type { MockStack, StackInspection } from '../lib/managed-mock';

test('walk mock offline directives expose presence, routers, images and scripted attempts', () => {
  const mock = new MockUpstream();
  expect(mock.baseUrl('fixture', { usage: 'absent', failures: 1, served: 'served-fixture', cost: 12.5, thinking: true, image: true }))
    .toBe('http://llm.e2e-public.test:8080/s/fixture/failures=1,usage=absent,served=served-fixture,cost=12.5,thinking=1,image=1');
  for (const options of [{ cost: NaN }, { cost: -1 }, { failures: -1 }, { served: 'http://vendor' }]) expect(() => mock.baseUrl('fixture', options)).toThrow();
});
test('walk mock offline parallel caller starts every call and preserves rejections', async () => {
  const started: number[] = []; let release!: () => void;
  const latch = new Promise<void>(resolve => { release = resolve; });
  const pending = parallelCalls([async () => { started.push(1); await latch; return 1; }, async () => { started.push(2); throw new Error('fixture'); }]);
  await Promise.resolve(); expect(started).toEqual([1,2]); release();
  const results = await pending; expect(results.map(r => r.status)).toEqual(['fulfilled','rejected']);
  await expect(parallelCalls([])).rejects.toThrow('1..64');
});
test('walk mock offline contract refuses nonloopback ports, changed commands and proxy env', () => {
  const stack = withWalkMocks({ services: [], publicMembers: [], sources: [], endpoints: [], profile: 'stock', check: () => undefined } as MockStack);
  const fixture = () => ({ selected: Object.fromEntries(WALK_MOCKS.map(service => [service, {
    Config: { Cmd: [...WALK_COMMANDS[service]], Env: [] }, NetworkSettings: { Ports: Object.fromEntries(Object.entries(WALK_PORTS[service])
      .map(([port, HostPort]) => [port, [{ HostIp:'127.0.0.1',HostPort }]])) },
  }])) }) as unknown as StackInspection;
  expect(() => stack.check(fixture())).not.toThrow();
  for (const service of WALK_MOCKS) {
    const badPort = fixture(); Object.values(badPort.selected[service].NetworkSettings.Ports)[0]![0].HostIp = '0.0.0.0';
    expect(() => stack.check(badPort)).toThrow('loopback only');
    const badCommand = fixture(); badCommand.selected[service].Config.Cmd.push('--forward');
    expect(() => stack.check(badCommand)).toThrow('command');
    const proxy = fixture(); proxy.selected[service].Config.Env.push('HTTPS_PROXY=https://vendor.invalid');
    expect(() => stack.check(proxy)).toThrow('proxy/preload');
  }
});
