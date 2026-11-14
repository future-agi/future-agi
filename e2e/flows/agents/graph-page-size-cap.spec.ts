import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';

// GraphViewSet.list/create and GraphCreateSerializer: name is the required create field.
const GRAPHS = '/agent-playground/graphs/';
// frontend/src/sections/agent-playground/AgentListView/AgentListView.jsx and DataTablePagination:
// the list starts at 25 rows, uses Search, and exposes the Next page button.
const AGENTS = '/dashboard/agents/playground';
// e2e/README.md first-paint budget; shared paginator's documented maximum is 100.
const UI_READY = 60_000;
const MAX_PAGE_SIZE = 100;

type Graph = { id: string; name: string };
type GraphPage = { result: { graphs: Graph[]; metadata: {
  total_count: number; page_number: number; page_size: number; total_pages: number;
  next_page: number | null; previous_page: number | null;
} } };

test('AGENT-E2E-002: Agents remain complete across bounded list pages', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'AGENT-E2E-002', area: 'agents',
    userGoal: 'A developer can browse all of their agents in bounded pages without losing rows or changing supported page sizes.',
    steps: [
      'create 101 uniquely named graphs through the public graph API',
      'open Agents Playground and search for the unique flow prefix',
      'compare the exact first-page names and IDs with the authorized 25-row list response',
      'move to page two and compare the exact next 25 rows with the list API',
      'request page sizes 101 and 100000000 and require a 100-row page with capped metadata',
      'follow next_page and require the final seeded graph without duplicated or omitted IDs',
    ],
    backendChecks: [
      'The UI displays the exact ordered graph set returned by the authorized list request at supported page sizes.',
      'Above-cap graph requests return at most 100 rows, metadata reflects the cap, and following next_page returns the complete seeded ID set once.',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // 180s for 101 sequential creates + 3 * 60s UI + 60s API/headroom.
  test.setTimeout(420_000);
  // Suppress third-party analytics; product traffic still reaches the real stack.
  const allowedOrigins = new Set([new URL(E2E.appUrl).origin, new URL(E2E.apiUrl).origin]);
  await page.route('**/*', route => allowedOrigins.has(new URL(route.request().url()).origin)
    ? route.continue() : route.abort());
  const prefix = `e2e-agent2-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const created: Graph[] = [];
  const attach = (name: string, value: unknown) => testInfo.attach(name, {
    body: JSON.stringify(value, null, 2), contentType: 'application/json',
  });
  try {
    for (let index = 0; index <= MAX_PAGE_SIZE; index++) {
      const name = `${prefix}-${String(index).padStart(3, '0')}`;
      const response = await actor.api.post<{ result: Graph }>(GRAPHS, { name });
      expect(response.result.name).toBe(name);
      created.push({ id: response.result.id, name });
    }
  } finally {
    // Preserve partial seeding evidence too, if an individual create fails.
    await attach('seeded-graphs', { prefix, graphs: created,
      organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  }
  // GraphViewSet.get_queryset orders newest first; sequential API creates have
  // distinct persisted timestamps. Expected identities come from create, not list.
  const expected = [...created].reverse();
  const names = page.getByText(new RegExp(`^${prefix}-[0-9]{3}$`));
  const waitList = (number: number, search?: string) => page.waitForResponse(response => {
    const url = new URL(response.url());
    return response.request().method() === 'GET' && url.pathname === GRAPHS
      && url.searchParams.get('page_size') === '25'
      && url.searchParams.get('page_number') === String(number)
      && (search === undefined || url.searchParams.get('search') === search);
  }, { timeout: UI_READY });

  await test.step('open Agents Playground', async () => {
    const response = waitList(1);
    await page.goto(AGENTS, { waitUntil: 'domcontentloaded' });
    expect((await response).status()).toBe(200);
  }, { timeout: UI_READY });

  for (const number of [1, 2]) {
    await test.step(number === 1 ? 'search the first page' : 'navigate to the next page', async () => {
      const responsePromise = waitList(number, prefix);
      if (number === 1) await page.getByPlaceholder('Search', { exact: true }).fill(prefix);
      else await page.getByRole('button', { name: 'Next page', exact: true }).click();
      const response = await responsePromise;
      expect(response.status()).toBe(200);
      const body = await response.json() as GraphPage;
      const replay = await actor.api.get<GraphPage>(GRAPHS, {
        search: prefix, page_size: 25, page_number: number,
      });
      const slice = expected.slice((number - 1) * 25, number * 25);
      await attach(`supported-page-${number}`, { body, replay });
      for (const result of [body.result, replay.result]) {
        expect(result.graphs.map(({ id, name }) => ({ id, name }))).toEqual(slice);
        expect(result.metadata).toEqual({ total_count: 101, page_number: number,
          page_size: 25, total_pages: 5, next_page: number + 1,
          previous_page: number === 1 ? null : number - 1 });
      }
      await expect(names).toHaveText(slice.map(graph => graph.name), { timeout: UI_READY });
      await testInfo.attach(`agents-page-${number}`, {
        body: await page.screenshot({ fullPage: true }), contentType: 'image/png',
      });
    }, { timeout: UI_READY });
  }

  for (const requestedSize of [101, 100000000]) {
    await test.step(`page_size=${requestedSize} is capped without losing rows`, async () => {
      const first = (await actor.api.get<GraphPage>(GRAPHS, {
        search: prefix, page_size: requestedSize, page_number: 1,
      })).result;
      await attach(`capped-${requestedSize}-first`, first);
      expect(first.graphs).toHaveLength(MAX_PAGE_SIZE);
      expect(first.graphs.map(({ id, name }) => ({ id, name }))).toEqual(expected.slice(0, MAX_PAGE_SIZE));
      expect(first.metadata).toEqual({ total_count: 101, page_number: 1,
        page_size: MAX_PAGE_SIZE, total_pages: 2, next_page: 2, previous_page: null });
      const last = (await actor.api.get<GraphPage>(GRAPHS, {
        search: prefix, page_size: requestedSize, page_number: first.metadata.next_page!,
      })).result;
      await attach(`capped-${requestedSize}-last`, last);
      expect(last.graphs.map(({ id, name }) => ({ id, name }))).toEqual(expected.slice(MAX_PAGE_SIZE));
      expect(last.metadata).toEqual({ total_count: 101, page_number: 2,
        page_size: MAX_PAGE_SIZE, total_pages: 2, next_page: null, previous_page: 1 });
      const ids = [...first.graphs, ...last.graphs].map(graph => graph.id);
      expect(ids).toEqual(expected.map(graph => graph.id));
      expect(new Set(ids).size).toBe(created.length);
    });
  }
});
