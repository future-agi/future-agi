import { getEventListeners } from 'node:events';
import { mock } from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { test, expect } from '@playwright/test';
import type { BrowserContext, Page, Route } from '@playwright/test';
import { ownedScopeOrigin, ScopeActors, scopeId, type ScopeActor } from '../lib/scope-actors';
import { AttachmentMediaDelivery, assertReverseDelivery, exerciseAppRefresh, mediaResolve,
  DELIVERY_READY, type DeliveryEvent } from '../lib/attachment-media-delivery';

// Offline guard/oracle/lifecycle tests only. No browser, network, cookies or grants.
// Synthetic token sentinels stay inside test doubles; never used to authenticate.
// These are NOT actual-browser refresh, request termination or timing qualification.
const USER = '10000000-0000-4000-8000-000000000001';
const ORG = '20000000-0000-4000-8000-000000000002';
const WORKSPACE = '30000000-0000-4000-8000-000000000003';
const events = (steps: [string, DeliveryEvent['phase']][]): DeliveryEvent[] =>
  steps.map(([operation, phase], index) => ({ operation, phase, sequence: index + 1 }));
const ordered = events([
  ['A', 'request'], ['A', 'backend-response'], ['B', 'request'],
  ['B', 'backend-response'], ['B', 'browser-delivery'], ['A', 'browser-delivery'],
]);

test('H13 unit: HTTPS does not relax owned localhost origin restrictions', () => {
  expect(ownedScopeOrigin('https://localhost:23443')).toBe('https://localhost:23443');
  expect(ownedScopeOrigin('http://localhost:3100/')).toBe('http://localhost:3100');
  for (const url of ['https://example.test', 'https://localhost.example.test', 'https://127.0.0.1',
    'https://name:password@localhost', 'https://localhost/path', 'https://localhost/?q=1',
    'https://localhost/#fragment', 'ftp://localhost']) {
    expect(() => ownedScopeOrigin(url)).toThrow('localhost HTTP(S) origin');
  }
  const scopes = new ScopeActors('unit', { appUrl: 'https://localhost:23443', ignoreHTTPSErrors: true });
  expect(scopes.options.ignoreHTTPSErrors).toBe(true);
  expect(() => new ScopeActors('unit', { appUrl: 'https://example.test', ignoreHTTPSErrors: true })).toThrow();
});

test('H13 unit: public IDs reject path fragments, nil and arbitrary strings without echoing them', () => {
  expect(scopeId(USER)).toBe(USER);
  expect(mediaResolve(ORG)).toBe(`/tracer/v1/projects/${ORG}/attachments/resolve`);
  for (const invalid of ['', 'not-a-public-identity', '00000000-0000-0000-0000-000000000000', `${ORG}/extra`]) {
    expect(() => scopeId(invalid)).toThrow('H1 expected a non-secret canonical UUID');
    expect(() => mediaResolve(invalid)).toThrow('H1 expected a non-secret canonical UUID');
  }
});

test('H13 unit: scope evidence is an explicit IDs-only projection', () => {
  const scopes = new ScopeActors('caller-supplied-name-must-not-be-attached');
  // Projection unit input, never passed to provisioning or an HTTP client.
  scopes.ownerA = { userId: USER, organizationId: ORG, workspaceId: WORKSPACE,
    email: 'unit-email-must-not-be-attached' } as ScopeActor;
  scopes.emptyWorkspace = { id: WORKSPACE, name: 'workspace-name-must-not-be-attached' };
  expect(scopes.evidence()).toEqual({ emptyWorkspaceId: WORKSPACE, limitations: [],
    actors: [{ userId: USER, organizationId: ORG, workspaceId: WORKSPACE }], requests: [] });
});

test('H13 unit: reverse oracle accepts only A-completed-before-B-issued and B-delivered-before-A', () => {
  expect(() => assertReverseDelivery(ordered, 'A', 'B')).not.toThrow();
  const concurrentIssuance = events([
    ['A', 'request'], ['B', 'request'], ['A', 'backend-response'],
    ['B', 'backend-response'], ['B', 'browser-delivery'], ['A', 'browser-delivery'],
  ]);
  expect(() => assertReverseDelivery(concurrentIssuance, 'A', 'B')).toThrow('complete A backend response before B issuance');
});

test('H13 unit: release-A-first mutation fails the unchanged B-before-A oracle', () => {
  const mutation = events([
    ['A', 'request'], ['A', 'backend-response'], ['B', 'request'],
    ['B', 'backend-response'], ['A', 'browser-delivery'], ['B', 'browser-delivery'],
  ]);
  expect(() => assertReverseDelivery(mutation, 'A', 'B')).toThrow('must deliver B before A');
});

test('H13 unit: missing or duplicate observations cannot certify delivery', () => {
  expect(() => assertReverseDelivery(ordered.slice(0, -1), 'A', 'B')).toThrow('exactly one event');
  expect(() => assertReverseDelivery([...ordered, ordered[0]], 'A', 'B')).toThrow('exactly one event');
});

test('H13 unit: media transport rejects HTTP and external origins before routing', () => {
  const noBrowser = {} as BrowserContext;
  expect(() => new AttachmentMediaDelivery(noBrowser, 'http://localhost:3100')).toThrow('requires HTTPS');
  expect(() => new AttachmentMediaDelivery(noBrowser, 'https://example.test', true)).toThrow('localhost');
  expect(() => new AttachmentMediaDelivery(noBrowser, 'https://localhost:23443', true)).not.toThrow();
});


for (const scenario of [
  { name: 'missing workspace', header: 'x-workspace-id', value: undefined },
  { name: 'wrong workspace', header: 'x-workspace-id', value: ORG },
  { name: 'wrong organization', header: 'x-organization-id', value: WORKSPACE },
  { name: 'wrong bearer and abort rejection', header: 'authorization', value: 'Bearer unit-wrong', abort: 'reject' },
  { name: 'terminated header read and synchronous abort failure', fail: 'headers', abort: 'throw', pendingTrigger: true },
  { name: 'terminated fulfillment and abort rejection', fail: 'fulfill', abort: 'reject', pendingTrigger: true },
] as const) {
  test(`H13 unit: refresh-trigger failure settles observations: ${scenario.name}`, async () => {
    // Native EventTargets let us prove BOTH bounded observation listeners are
    // removed, not merely that Promise.all rejected while one signal leaked.
    // Controlled deadlines also let a broken implementation clean up without
    // sleeping or waiting DELIVERY_READY. No app/browser clock is modified.
    const deadlines: AbortController[] = [];
    const timeout = mock.method(AbortSignal, 'timeout', (delay: number) => {
      expect(delay).toBe(DELIVERY_READY);
      const controller = new AbortController();
      deadlines.push(controller);
      return controller.signal;
    });
    const before = { access: 'unit-access-sentinel', refresh: 'unit-refresh-sentinel',
      organizationId: ORG, workspaceId: WORKSPACE };
    const raw = new Error('H3 transport details: Bearer unit-access-sentinel; Cookie=unit-cookie-sentinel');
    const headers: Record<string, string | undefined> = { authorization: `Bearer ${before.access}`,
      'x-organization-id': ORG, 'x-workspace-id': WORKSPACE };
    if ('header' in scenario && typeof scenario.header === 'string') headers[scenario.header] = scenario.value;
    let handler!: (route: Route) => Promise<void>;
    let routeTask: Promise<void> | undefined;
    let aborts = 0, fulfillments = 0, fallbacks = 0, unroutes = 0;
    let aborted = false, routeError: unknown, observedError: unknown;
    let releaseTrigger!: () => void;
    const triggerDone = new Promise<void>(resolve => { releaseTrigger = resolve; });
    const listeners = new Map<string, unknown>();
    const unhandled: unknown[] = [];
    const onUnhandled = (error: unknown) => { unhandled.push(error); };
    process.on('unhandledRejection', onUnhandled);
    const page = {
      url: () => 'https://localhost:23443/dashboard/observe',
      evaluate: async () => before,
      on(event: string, listener: unknown) { listeners.set(event, listener); return this; },
      off(event: string, listener: unknown) {
        expect(listeners.get(event)).toBe(listener);
        listeners.delete(event); return this;
      },
      route: async (_matches: unknown, callback: typeof handler) => { handler = callback; },
      unroute: async (_matches: unknown, callback: typeof handler) => {
        expect(callback).toBe(handler); unroutes += 1;
      },
    } as unknown as Page;
    const request = {
      method: () => 'GET',
      allHeaders: async () => {
        if ('fail' in scenario && scenario.fail === 'headers') throw raw;
        return headers;
      },
    };
    const route = {
      request: () => request,
      fallback: async () => { fallbacks += 1; },
      fulfill: async () => { fulfillments += 1; throw raw; },
      abort: (code: string) => {
        expect(code).toBe('failed'); aborts += 1;
        if ('abort' in scenario && scenario.abort === 'throw') throw raw;
        if ('abort' in scenario && scenario.abort === 'reject') return Promise.reject(raw);
        aborted = true; return Promise.resolve();
      },
    } as unknown as Route;
    const execution = exerciseAppRefresh(page, async () => {
      routeTask = handler(route);
      void routeTask.catch(error => { routeError = error; });
      // Navigation can still be pending after a terminated route. The helper
      // must reject from observations instead of waiting for this trigger.
      if ('pendingTrigger' in scenario) await triggerDone;
      else await routeTask;
    });
    void execution.catch(error => { observedError = error; });
    try {
      await setImmediate(); // Drain promise/event work; no wall-clock sleep.
      expect(observedError).toBeInstanceOf(Error);
      expect((observedError as Error).message)
        .toBe('H3 ordinary-read fault injection failed (request details omitted)');
      expect((observedError as Error).cause).toBeUndefined();
      for (const sentinel of [before.access, before.refresh, 'unit-cookie-sentinel', 'unit-wrong']) {
        expect(String((observedError as Error).stack)).not.toContain(sentinel);
      }
      expect(aborts).toBe(1);
      expect(aborted).toBe(!('abort' in scenario));
      expect(fulfillments).toBe('fail' in scenario && scenario.fail === 'fulfill' ? 1 : 0);
      expect(fallbacks).toBe(0);
      expect(routeError).toBeUndefined();
      expect(unroutes).toBe(1);
      expect(listeners.size).toBe(0);
      expect(deadlines).toHaveLength(3);
      for (const controller of deadlines.slice(0, 2)) {
        expect(getEventListeners(controller.signal, 'abort')).toHaveLength(0);
      }
      expect(unhandled).toEqual([]);
    } finally {
      releaseTrigger();
      for (const controller of deadlines) controller.abort();
      await Promise.allSettled([execution, ...(routeTask ? [routeTask] : [])]);
      await setImmediate();
      process.off('unhandledRejection', onUnhandled);
      timeout.mock.restore();
    }
    expect(unhandled).toEqual([]);
  });
}
