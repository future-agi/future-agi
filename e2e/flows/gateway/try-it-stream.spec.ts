import type { Request, Response } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { POLL } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';

// Pinned off the source:
// - the model page's Try it tab: /dashboard/gateway/models/<provider>/<model>?tab=try
//   (models/modelLinks.js modelPath, the id split at its first "/";
//   models/detail/ModelTryIt.jsx): fields "System prompt (optional)" and "Prompt",
//   the "Parameters" group (detail/TryParameters.jsx, tryParams.js PARAM_FIELDS:
//   "Temperature", "Max tokens", "Top P"; empty sends nothing), the "Stream the
//   answer" switch (on by default), "Run", which becomes "Stop" while a streamed
//   answer runs, the answer box, and once done a "Request id" figure.
// - whether the deployment offers managed models: GET /agentcc/managed-models/
//   `available` (agentcc/views/managed_models.py list). Where it offers none, GET
//   /agentcc/managed-models/catalog/ answers { available: false, markup_percent: null,
//   providers: [], models: [] } (the same view's catalog).
// - which model Try it calls (detail/modelFacts.js callTarget):
//   - managed models offered (catalogRows.js ownKeyRoutes): with managed models off
//     for the org, a catalog model its own provider serves, where the provider's id
//     is the catalog vendor and it lists the model's part after the "/"; Try it then
//     sends that part.
//   - none offered (models/orgServed.js): there is no catalog, and the page of a
//     model the org's own provider serves is /models/<provider id>/<listed model>;
//     Try it sends that id, "<provider id>/<listed model>", which the gateway routes
//     to that provider (agentcc-gateway server/handlers.go orgModelMatches) and logs
//     as requested (plugins/logging/record.go Model: rc.Model); the provider is sent
//     the model it lists (server/org_upstream_model.go orgModelFor).
//   Its providers come from GET /agentcc/gateways/default/providers/ (id =
//   provider_name, models = models_list).
// - the wire (components/gateway-test-request.js gatewayTestBody,
//   streamGatewayTest): POST /agentcc/gateways/<id>/test-playground-stream/
//   { prompt, model, temperature?, max_tokens?, top_p? }, answered as server-sent
//   events (agentcc/services/playground_stream.py): "meta" (status_code,
//   guardrail_headers: the gateway's x-agentcc-* headers), one "delta"
//   { content } per piece of the answer as it arrives, then "done". Stop aborts the
//   browser's request (an AbortController signal).
// - the mock's scoped routes (e2e/stack/mock-llm/server.mjs, lib/upstream.ts):
//   with stream true it answers one chunk per word of "echo: <last user message>";
//   lat=<ms> holds the answer that long; each request is recorded with its body.
// - the gateway's own record: agentcc_request_log by request_id (log_ingestion.py).
const MANAGED_STATUS_PATH = '/agentcc/managed-models/';
const CATALOG_PATH = '/agentcc/managed-models/catalog/';
const PROVIDERS_PATH = '/agentcc/provider-credentials/';
const STREAM_PATH = /\/agentcc\/gateways\/[^/]+\/test-playground-stream\/$/;
const MODELS_PATH = '/dashboard/gateway/models';
// Provider ids the stack's own gateway config already uses (e2e/stack/gateway.e2e.yaml).
const NODE_PROVIDERS = new Set(['openai', 'fagi-managed']);
const PARAMS = { temperature: 0.3, max_tokens: 64, top_p: 0.9 };
// Long enough that Stop lands while the provider is still holding its answer.
const SLOW_MS = 30_000;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
const PUSH_VISIBLE = POLL.ASYNC_JOB; // the provider create's push reaching the gateway's copy
const LOG_VISIBLE = POLL.ASYNC_JOB; // the gateway's log flush reaching Django's ingest
// scopeActors provisioning 120 + seeding 15 + PUSH_VISIBLE 60 + the streamed run step
// 75 + LOG_VISIBLE 60 + the Stop step 75 + headroom 30 = 435 s, rounded up.
const FLOW_TIMEOUT = 450_000;

// A model Try it calls: its page id, the org provider that serves it, the model
// that provider lists, and the model Try it sends.
interface Target { id: string; provider: string; listed: string; sendAs: string }

interface CatalogModel {
  id: string;
  vendor?: string;
  endpoint_type?: string;
  output_modalities?: string[];
  supported_parameters?: string[];
}
type Created<T> = { result: T };
type Catalog = { available?: boolean; markup_percent: number | null; providers: unknown[]; models: CatalogModel[] };
type StreamEvent = { event: string; data: Record<string, unknown> };

// components/gateway-test-request.js parseEvents: complete events only.
function parseEvents(text: string): StreamEvent[] {
  const blocks = text.split('\n\n');
  blocks.pop();
  return blocks.flatMap((block) => {
    let event = 'message';
    let data = '';
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) data += line.slice(5).trim();
    }
    return data ? [{ event, data: JSON.parse(data) as Record<string, unknown> }] : [];
  });
}

// A catalog model Try it can call through an own provider named after its vendor:
// a plain "<vendor>/<model>" id under that vendor, chat with text output only
// (catalogRows.js modelType), taking all three parameters (tryParams.js takes: a
// model with no list takes any), and a vendor the stack's gateway does not already name.
const tryable = (m: CatalogModel) => {
  const match = /^([a-z0-9][a-z0-9-]*)\/([a-z0-9][^/\s]*)$/.exec(m.id);
  const listed = m.supported_parameters;
  return Boolean(match) && m.vendor === match![1] && !NODE_PROVIDERS.has(match![1]) &&
    [undefined, 'chat'].includes(m.endpoint_type) && (m.output_modalities ?? []).every((o) => o === 'text') &&
    (!listed?.length || (['temperature', 'top_p'].every((p) => listed.includes(p)) &&
      (listed.includes('max_tokens') || listed.includes('max_completion_tokens'))));
};
const modelPagePath = (id: string) => {
  const at = id.indexOf('/');
  return `${MODELS_PATH}/${encodeURIComponent(id.slice(0, at))}/${encodeURIComponent(id.slice(at + 1))}`;
};

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-019: Try it on a model page streams the answer of a model the organization\'s own provider serves, with the sampling parameters set, and Stop ends a slow one',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-019',
      area: 'gateway',
      userGoal:
        'A developer tries a model their own provider key serves from its model page, with managed models offered or not, with a temperature, a token limit and a top P, reads the answer as it streams in, and stops an answer that is taking too long',
      steps: [
        'read whether the deployment offers managed models, and its catalog',
        'seed two own providers on the echo mock, the second one slow to answer: with managed models offered, each named after a catalog vendor and listing one of its chat models; with none, each with a minted id and model',
        'open the first model\'s page on its Try it tab',
        'set Temperature 0.3, Max tokens 64 and Top P 0.9, type a minted prompt and Run with Stream the answer on',
        'read the streamed answer and its request id',
        'open the second model\'s Try it tab, Run, and press Stop once its provider has the request',
      ],
      backendChecks: [
        'where no managed models are offered, the catalog answers available false with no providers and no models',
        'the browser sends test-playground-stream exactly the prompt, the model (as the provider lists it with managed models offered, else the page id naming the provider) and the three parameters',
        'the answer arrives as server-sent events: meta, then one delta per word of the mock\'s echo that join to exactly the echo, then done',
        'the first provider received exactly one chat completion, streamed, with the prompt and the three parameters',
        'the gateway logged the request under the organization: the request id the page shows, the model Try it sent, status 200',
        'the second provider received the request before Stop, and Stop aborted the browser\'s stream',
      ],
      browserChecks: [
        'the answer box shows exactly the mock\'s echo of the prompt, and the page shows the request id the gateway sent',
        'after Stop the page offers Run again with no answer and no error',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const upstream = new MockUpstream();
    const fast = `gw19-${suffix}-a`;
    const slow = `gw19-${suffix}-b`;
    const prompt = `e2e gw19 ${suffix} three more words`;
    const echo = `echo: ${prompt}`;

    // The shape this deployment must show: with managed models offered, or with none.
    const managed = (await owner.api.get<Created<{ available: boolean }>>(MANAGED_STATUS_PATH))
      .result.available === true;
    const catalog = (await owner.api.get<Created<Catalog>>(CATALOG_PATH)).result;
    let first: Target;
    let second: Target;
    if (managed) {
      // Two catalog models of two vendors (code-point order), so each provider is its
      // vendor's and serves only its own model; Try it sends the model it lists.
      const candidates = [...catalog.models].filter(tryable)
        .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
      const a = candidates[0];
      const b = candidates.find((m) => m.vendor !== a?.vendor);
      expect({ first: Boolean(a), second: Boolean(b) }, 'two tryable catalog models of two vendors')
        .toEqual({ first: true, second: true });
      const target = (m: CatalogModel): Target => {
        const listed = m.id.slice(m.id.indexOf('/') + 1);
        return { id: m.id, provider: m.vendor!, listed, sendAs: listed };
      };
      [first, second] = [target(a), target(b!)];
    } else {
      // No catalog: two minted providers, each serving one minted model, called by
      // the page id that names the provider.
      expect(catalog, 'the catalog of a deployment with no managed models')
        .toEqual({ available: false, markup_percent: null, providers: [], models: [] });
      const target = (part: string): Target => {
        const provider = `gw19p-${suffix}-${part}`;
        const listed = `gw19-chat-${part}`;
        return { id: `${provider}/${listed}`, provider, listed, sendAs: `${provider}/${listed}` };
      };
      [first, second] = [target('a'), target('b')];
    }
    for (const [t, baseUrl] of [
      [first, upstream.baseUrl(fast)],
      [second, upstream.baseUrl(slow, { latencyMs: SLOW_MS })],
    ] as const) {
      await owner.api.post<Created<{ id: string }>>(PROVIDERS_PATH, {
        provider_name: t.provider, credentials: { api_key: `sk-e2e-gw19-${suffix}` }, base_url: baseUrl,
        api_format: 'openai', models_list: [t.listed], extra_config: { api_path_prefix: '/v1' },
      });
    }
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, organizationId: owner.organizationId, prompt, managedModelsOffered: managed,
        first: { ...first, scenario: fast }, second: { ...second, scenario: slow } }, null, 2),
    });

    const admin = await GatewayAdmin.open();
    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const tryIt = page.getByRole('region', { name: 'Try it', exact: true });
    const sent: Request[] = [];
    const aborted: string[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && STREAM_PATH.test(new URL(r.url()).pathname)) sent.push(r);
    });
    page.on('requestfailed', (r) => {
      if (STREAM_PATH.test(new URL(r.url()).pathname)) aborted.push(r.failure()?.errorText ?? '');
    });
    try {
      await test.step('gateway: both own providers are pushed (precondition)', async () => {
        await expect.poll(async () => Object.keys((await admin.orgConfig(owner.organizationId))?.providers ?? {}).sort(),
          PUSH_VISIBLE).toEqual(expect.arrayContaining([first.provider, second.provider]));
      });

      let events: StreamEvent[] = [];
      await test.step('UI + wire: Run streams the answer with the three parameters', async () => {
        await page.goto(`${modelPagePath(first.id)}?tab=try`, { waitUntil: 'domcontentloaded' });
        await expect(tryIt).toBeVisible({ timeout: UI_READY });
        const params = tryIt.getByRole('group', { name: 'Parameters' });
        await params.getByLabel('Temperature', { exact: true }).fill(String(PARAMS.temperature));
        await params.getByLabel('Max tokens', { exact: true }).fill(String(PARAMS.max_tokens));
        await params.getByLabel('Top P', { exact: true }).fill(String(PARAMS.top_p));
        await tryIt.getByLabel('Prompt', { exact: true }).fill(prompt);
        await expect(tryIt.getByRole('checkbox', { name: 'Stream the answer' })).toBeChecked();
        const answered = page.waitForResponse(
          (r: Response) => r.request().method() === 'POST' && STREAM_PATH.test(new URL(r.url()).pathname),
          { timeout: UI_READY });
        await tryIt.getByRole('button', { name: 'Run', exact: true }).click();
        const response = await answered;
        expect(response.status()).toBe(200);
        expect(response.headers()['content-type']).toContain('text/event-stream');
        expect(sent.map((r) => r.postDataJSON())).toEqual([{ prompt, model: first.sendAs, ...PARAMS }]);
        events = parseEvents(await response.text());
        await testInfo.attach('events', { contentType: 'application/json', body: JSON.stringify(events, null, 2) });
        const deltas = events.filter((e) => e.event === 'delta').map((e) => String(e.data.content));
        expect({
          order: [events[0]?.event, events.at(-1)?.event],
          answer: deltas.join(''),
          pieces: deltas.length,
        }).toEqual({ order: ['meta', 'done'], answer: echo, pieces: echo.split(/(?<=\s)/).length });
        await expect(tryIt.getByText(echo, { exact: true })).toBeVisible({ timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('upstream + storage: the provider got the parameters, the request is logged under the org', async () => {
        const seen = (await upstream.requests(fast)).map((r) => r.body as Record<string, unknown>);
        expect(seen).toHaveLength(1);
        expect(seen[0]).toMatchObject({ model: first.listed, stream: true, ...PARAMS });
        expect((seen[0].messages as { role: string; content: string }[]).at(-1)).toEqual({ role: 'user', content: prompt });
        const headers = Object.fromEntries(Object.entries(
          (events.find((e) => e.event === 'meta')?.data.guardrail_headers ?? {}) as Record<string, string>)
          .map(([k, v]) => [k.toLowerCase(), v]));
        const requestId = headers['x-agentcc-request-id'] ?? '';
        expect(requestId).not.toBe('');
        await testInfo.attach('request', { contentType: 'application/json', body: JSON.stringify({ requestId }) });
        await expect(tryIt.getByText(requestId, { exact: true })).toBeVisible({ timeout: UI_READY });
        await expect.poll(async () => probe.pg<{ model: string; status_code: number }>(
          'SELECT model, status_code FROM agentcc_request_log WHERE organization_id = $1 AND request_id = $2',
          [owner.organizationId, requestId]), LOG_VISIBLE).toEqual([{ model: first.sendAs, status_code: 200 }]);
      });

      await test.step('UI + upstream: Stop ends a slow answer and Run comes back', async () => {
        await page.goto(`${modelPagePath(second.id)}?tab=try`, { waitUntil: 'domcontentloaded' });
        await expect(tryIt).toBeVisible({ timeout: UI_READY });
        await tryIt.getByLabel('Prompt', { exact: true }).fill(prompt);
        await tryIt.getByRole('button', { name: 'Run', exact: true }).click();
        const stop = tryIt.getByRole('button', { name: 'Stop', exact: true });
        await expect(stop).toBeVisible({ timeout: UI_READY });
        // Precondition: the slow provider has the request, so Stop lands mid-answer.
        await expect.poll(async () => (await upstream.requests(slow)).length, { timeout: UI_READY }).toBe(1);
        await stop.click();
        await expect(tryIt.getByRole('button', { name: 'Run', exact: true })).toBeEnabled({ timeout: UI_READY });
        await expect.poll(() => aborted, { timeout: UI_READY }).toEqual([expect.stringMatching(/ABORTED/i)]);
        await expect(tryIt.getByText(echo, { exact: true })).toHaveCount(0);
        await expect(tryIt.getByRole('alert')).toHaveCount(0);
        expect(sent).toHaveLength(2);
      }, { timeout: STEP_BUDGET });
    } finally {
      await admin.dispose();
      await context.close();
    }
  },
);
