import { createHash } from 'node:crypto';
import { expect } from '../../lib/walk/fixtures';
import { test, type CoreRuntime } from './console-core-seams';
import type { APIRequestContext, TestInfo } from '@playwright/test';
import type { ScopeActors } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { gatewayProfile } from '../../lib/profile';
import { JOURNEY_PUBLICATION_ACCEPTED, JOURNEY_PUBLICATION_GAP } from './publication-contract';
import { runJ03ConsolePositive } from './j03-console-positive';

// JOURNEYS-708 v2 J03: applies_when is from LM-708 served[], NOT UI exposure.
// A missing component must pass its if_absent branch. Missing harness evidence
// is explicitly BLOCKED instead; it is never a successful absence check.
const FLOW = 'JRN-E2E-003';
// Preserve every assertion, but do not provision through an unaccepted
// publication boundary. This definition-time skip also guards direct runs.
test.skip(!JOURNEY_PUBLICATION_ACCEPTED, JOURNEY_PUBLICATION_GAP);
// HR-JRN-02 is still owed for integrated acceptance, not for always legs.
const LEGACY_GAP = 'HR-JRN-09: require a test-only legacy Cell.dataset=NULL seeder with preserved row/column parents; StateProbe is read-only and the public create path always sets dataset';
const UI_READY = 60_000;
// model_hub/urls.py and contracts.py ManualDatasetCreateRequestSerializer.
const CREATE_DATASET = '/model-hub/develops/create-dataset-manually/';
// DatasetUpdateCellValueRequestSerializer, UpdateCellValueView.post; no SQL writes.
const cellPath = (id: string) => `/model-hub/develops/${id}/update_cell_value/`;
// DatasetTableQuerySerializer and GetDatasetTableView: result.table; cell_value.
const tablePath = (id: string) => `/model-hub/develops/${id}/get-dataset-table/`;
const tableParams = { current_page_index: 0, page_size: 10, filters: '[]', sort: '[]' };
const fingerprint = (value: unknown) => `sha256:${createHash('sha256').update(JSON.stringify(value)).digest('hex')}`;
type Created = { result: { dataset_id: string; rows_created: number; columns_created: number } };
type Cell = { id: string; dataset_id: string | null; row_id: string; column_id: string; value: string | null };
type Table = { result: { table: Array<{ row_id: string } & Record<string, unknown>> } };

// The inherited default includes a Viewer in A. Adding a B Member gives at least
// two users in each org without constructing identities inside this spec.
test.use({ scopeRoster: { orgB: [3] }, trace: 'off', screenshot: 'off', video: 'off' });

const legs = [
  {
    id: 'L1', title: 'ISO-08 ordinary cell write; legacy subcase tracked separately', applies_when: 'always',
    flags_required: ['accepted ISO-08 cell-authorization block (span hash c658aa20) present in LM-708 FA source'],
    steps: ['Org A creates a dataset, rows, columns and cells',
      'Org B posts update_cell_value with A dataset/row/column IDs, including legacy Cell.dataset=NULL',
      'After EVERY B attempt, read A data as A and verify it is unchanged'],
    pass_criteria: 'Every cross-tenant write is 403/404; A cell unchanged by authenticated A backend read',
    if_absent: null,
    gap: 'Ordinary direct-parent and mismatched-parent cells execute here. Legacy NULL cells are a separately reported subcase (HR-JRN-09), never a skip of the ordinary-cell test.',
  },
  {
    id: 'L2', title: 'Knowledge-base scope', applies_when: 'always',
    flags_required: ['ISO-08 KB follow-up (686 add.3) present in LM-708 FA source'],
    steps: ['Org A creates a knowledge base',
      'Org B submits its ID to the eval playground and attempts KB enumeration',
      'After each attempt read the KB and its files as A and compare with the pre-attempt state'],
    pass_criteria: '403/404 for foreign references; no unscoped KB enumeration; bounded resource use and no A mutation',
    if_absent: null,
    gap: 'HR-JRN-11: require a deterministic KB/file fixture and resource-use/dispatch reader for the playground. Existing uploadFixture only supports dataset CSV; no model/provider request may be used as an authorization probe',
  },
  {
    id: 'L3', title: 'DSX grid and experiment writes', applies_when: 'always',
    flags_required: ['MADE_REAL registry (probe the API directly, not the UI)'],
    steps: ['As B call row duplicate, row delete, column and experiment-configuration endpoints with A IDs',
      'Include revoked template with a new version pin and unchanged foreign KB reference (ruling 700)',
      'After EVERY attempted write read the affected dataset/configuration/template/KB as A'],
    pass_criteria: '403/404 with zero change; stricter-parent rules hold even when the UI is hidden',
    if_absent: null,
    gap: 'HR-JRN-12: require revoked-template/version-pin and foreign-KB fixtures plus a complete org-A graph reader; ordinary row denial alone cannot close this unconditional leg',
  },
  {
    id: 'L4', title: 'Results API', applies_when: 'served:results-api',
    flags_required: ['Results backend composed (UI exposure irrelevant)'],
    steps: ['As B request A dataset/run runs, results and analysis with baseline=none, pinned baseline and mixed evaluator versions',
      'Check loading/error paths for comparative output and read A state after every attempt'],
    pass_criteria: '403/404; no comparative output without validated scope; A unchanged',
    if_absent: {
      steps: ['As members of both orgs GET /model-hub/datasets/{id}/runs/, /results/ and /results/analysis/',
        'Read frontend contract exceptions for those same paths'],
      pass_criteria: '404 for every org AND frontend exceptions mark all paths unavailable',
    },
    gap: 'HR-JRN-07/12: require deployed contract-exception reader and Results run/baseline/mixed-version fixtures. Missing Results must run the three-route denial matrix, not skip because its UI is off',
  },
  {
    id: 'L5', title: 'Console through the Node edge', applies_when: 'served:omega-edge & served:ee-daemon & admitted:console',
    flags_required: ['VITE_OMEGA_CONSOLE (runtime config)', 'console rollout stage: test orgs A and B only',
      'console-route-manifest union digest'],
    steps: ['A creates a Console agent, conversation with agentId and audit row',
      'B addresses A agent/run/events/cancel/audit using its own valid browser assertion and personal API key',
      'Pre-mint a B assertion and replay it twice; read A state after every attempt'],
    pass_criteria: 'Every foreign call refused; second assertion use refused; A unchanged',
    if_absent: {
      steps: ['Run J00.L3 absence requests for both orgs',
        'If session exchange is served, mint for each org and inspect the products claim'],
      pass_criteria: 'No Console surface reachable and no console product claim minted',
    },
    gap: 'HR-JRN-05/13: require edge/browser-assertion and personal-key actors, session-exchange claim reader and stream/run/audit seed/readers. Reuse J00.L3 ingress/DNS proof; never infer absence from Django alone',
  },
  {
    id: 'L6', title: 'Gateway keys, MCP and max-charge scopes', applies_when: 'served:gateway-mcp',
    flags_required: ['mcp profile (walk VM-3a)', 'MCP apps', 'max-charge header'],
    steps: ['A creates gateway key, MCP connection and virtual server; create max-charge scope if maxcharge-685 is served',
      'B uses its own scoped gateway key against each A ID; read A connection/server/scope after each attempt'],
    pass_criteria: '403/404 with zero change and no foreign credential use',
    if_absent: {
      steps: ['With MCP disabled in the profile call MCP and virtual-server routes as both orgs',
        'Read credential rows before and after each call'],
      pass_criteria: 'MCP disabled for every org; no MCP credential created',
    },
    gap: 'HR-JRN-14: require scoped gateway request actor and MCP/max-charge fixture/readers for both profiles. gatewayProfile/PROFILE_SERVES_MCP already declares MCP but not LM-708 maxcharge-685 or deployed credential-use evidence',
  },
  {
    id: 'L7', title: 'Sandboxes', applies_when: 'served:ee-sandbox-engine & env:selfhost-rig',
    flags_required: ['OMEGA_WORKSPACE_SANDBOXES', 'OMEGA_WORKSPACE_SANDBOX_NETNS'],
    steps: ['On the selfhost rig A creates a sandbox',
      'B lists, reads, writes and deletes it by ID; read A sandbox after each attempt'],
    pass_criteria: '403/404 on every attempt; A sandbox intact',
    if_absent: {
      steps: ['On hosted request sandbox list and create as both orgs'],
      pass_criteria: 'Both refused for every org; hosted withheld and no sandbox plane',
    },
    gap: 'HR-JRN-08/13: require EE sandbox actors and an engine-bound intact-state reader; absent EE still requires authenticated list/create refusals on the public ingress',
  },
  {
    id: 'L8', title: 'Repeat after restart and deployment replacement', applies_when: 'always',
    flags_required: [],
    steps: ['Retain the same two-org fixtures and run EVERY applicable leg (including absence branches)',
      'Restart services, repeat; replace deployment, repeat; bind every result to the same source/contract digest'],
    pass_criteria: 'Identical complete results after both transitions; A unchanged after every B attempt',
    if_absent: null,
    gap: 'HR-JRN-15: require managed service-restart and replacement controls, retained-fixture replay and per-phase receipts. Fresh ScopeActors per test cannot prove continuity; do not restart a shared stack from a spec',
  },
] as const;

for (const leg of legs) {
  // HR-JRN-10: catalog currently requires unique IDs per test. Composite IDs
  // retain the reserved journey prefix without silently omitting @flow tags.
  const id = `${FLOW}.${leg.id}`;
  const details = {
    tag: ['@flow', '@journey', '@journey-J03'],
    annotation: [
      flowAnnotation({ id, area: 'journeys', userGoal: `Release obligation: ${leg.title}`,
        steps: leg.steps.map(step => `Full release contract (includes owed subcases): ${step}`), backendChecks: [
          ...(['L1', 'L2', 'L3', 'L4', 'L5', 'L7'].includes(leg.id)
            ? ['Executable ordinary-cell, KB enumeration, grid-write or absent HTTP checks; A backend read follows every B attempt'] : []),
          `Remaining subcase dependency: ${leg.gap}`,
          `Release criterion: ${leg.pass_criteria}`,
        ] }),
      { type: 'journey-flow-id', description: FLOW },
      { type: 'journey-contract', description: JSON.stringify({ journey: 'J03', ...leg }) },
    ],
  };

  if (leg.id !== 'L1') {
    const body = async ({ scopeActors: scopes, scopeProbe: probe, request }: {
      scopeActors: ScopeActors; scopeProbe: StateProbe; request: APIRequestContext;
    }, testInfo: TestInfo, runtime?: CoreRuntime) => {
      test.setTimeout(120_000);
      // Same canonical L5 case. Unresolved LM records BLOCKED but still permits
      // the standalone-stock historical rehearsal below, ending in its old skip.
      if (leg.id === 'L5' && await runJ03ConsolePositive(testInfo, runtime,
        gatewayProfile() === 'stock' && process.env.E2E_STACK === 'standalone')) return;
      if (leg.id === 'L6' || leg.id === 'L8') test.skip(true, leg.gap);
      const A = scopes.ownerA;
      const B = scopes.ownerB;
      const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
      if (leg.id === 'L2') {
        // LegacyKnowledgeBaseMutationRequestSerializer and CreateKnowledgeBaseView
        // accept an empty file set: this seeds the *same* KnowledgeBaseFile model
        // used by the playground, without an embedding provider or SQL writes.
        type KBList = { result: { table_data: { id: string; name: string }[] } };
        const seeded = await scopes.send<{ result: { kb_id: string } }>(A, 'POST',
          '/model-hub/knowledge-base/', { name: `e2e-j03-kb-${suffix}` });
        expect(seeded.status, 'public empty KB seed').toBe(200);
        const kb = seeded.body.result.kb_id;
        const before = await scopes.send<KBList>(A, 'GET', '/model-hub/knowledge-base/list/');
        expect(before.status).toBe(200);
        expect(before.body.result.table_data.some(row => row.id === kb), 'A sees its seeded KB').toBe(true);
        const baseline = fingerprint(before.body.result.table_data);
        try {
          const other = await scopes.send<KBList>(B, 'GET', '/model-hub/knowledge-base/list/');
          expect.soft(other.status).toBe(200);
          expect.soft(other.body.result.table_data.some(row => row.id === kb), 'L2 B cannot enumerate A KB').toBe(false);
        } finally {
          const after = await scopes.send<KBList>(A, 'GET', '/model-hub/knowledge-base/list/');
          expect.soft(after.status).toBe(200);
          expect.soft(fingerprint(after.body.result.table_data) === baseline, 'L2 A KB unchanged after B enumeration').toBe(true);
          await testInfo.attach('j03-l2-enumeration', { contentType: 'application/json',
            body: JSON.stringify({ kb: fingerprint(kb), unchanged: fingerprint(after.body.result.table_data) === baseline }) });
        }
        expect(testInfo.errors.length, 'L2 executed assertions').toBe(0);
        test.skip(true, 'HR-JRN-11: empty KB enumeration executed; playground needs deterministic authorized template and bounded dispatch/file reader');
      }
      const created = await scopes.send<Created>(A, 'POST', CREATE_DATASET,
        { dataset_name: `e2e-j03-${leg.id}-${suffix}`, number_of_rows: 1, number_of_columns: 1 });
      expect(created.status).toBe(200);
      const dataset = created.body.result.dataset_id;
      const before = await scopes.send<Table>(A, 'GET', tablePath(dataset), undefined, tableParams);
      expect(before.status).toBe(200);
      expect(before.body.result.table.length).toBe(1);
      const baseline = fingerprint(before.body.result.table);
      // PG graph includes new/deleted rows, cells and renamed columns, which the
      // table's data rows alone would not detect. All reads are constrained to A.
      const graphQuery = `SELECT 'row' AS kind, to_jsonb(r)::text AS state FROM model_hub_row r
        JOIN model_hub_dataset d ON d.id = r.dataset_id WHERE d.id = $1 AND d.organization_id = $2
        UNION ALL SELECT 'column', to_jsonb(c)::text FROM model_hub_column c
        JOIN model_hub_dataset d ON d.id = c.dataset_id WHERE d.id = $1 AND d.organization_id = $2
        UNION ALL SELECT 'cell', to_jsonb(c)::text FROM model_hub_cell c
        JOIN model_hub_dataset d ON d.id = c.dataset_id WHERE d.id = $1 AND d.organization_id = $2
        ORDER BY kind, state`;
      const graphBefore = fingerprint(await probe.pg(graphQuery, [dataset, A.organizationId]));
      const attempts: Array<{ method: string; path: string; data?: unknown }> = [];
      if (leg.id === 'L3') {
        const cells = await probe.pg<Cell>(
          'SELECT id::text, row_id::text, column_id::text, dataset_id::text, value FROM model_hub_cell WHERE dataset_id = $1 AND NOT deleted', [dataset]);
        expect(cells.length).toBe(1);
        // DuplicateRowsRequestSerializer, DeleteRowView.delete and
        // UpdateColumnNameView.put; each request is pinned to a real owned parent.
        attempts.push({ method: 'POST', path: `/model-hub/datasets/${dataset}/duplicate-rows/`,
          data: { row_ids: [cells[0].row_id], selected_all_rows: false, num_copies: 1 } },
        { method: 'PUT', path: `/model-hub/develops/${dataset}/update_column_name/${cells[0].column_id}/`,
          data: { new_column_name: `e2e-foreign-${suffix}` } },
        { method: 'DELETE', path: `/model-hub/develops/${dataset}/delete_row/`,
          data: { row_ids: [cells[0].row_id], selected_all_rows: false } });
      } else {
        test.skip(gatewayProfile() !== 'stock' || process.env.E2E_STACK !== 'standalone',
          'HR-JRN-02: integrated served/admitted profile required; standalone stock absence rehearsal only');
        if (leg.id === 'L4') for (const tail of ['runs/', 'results/', 'results/analysis/']) {
          attempts.push({ method: 'GET', path: `/model-hub/datasets/${dataset}/${tail}` });
        }
        if (leg.id === 'L5') for (const path of ['/omega/api/workspace/capabilities', '/omega/console/']) {
          attempts.push({ method: 'GET', path });
        }
        // JOURNEYS J03.L7; EE workspace sandbox router, via the public edge.
        if (leg.id === 'L7') for (const method of ['GET', 'POST']) {
          attempts.push({ method, path: '/omega/api/workspace/sandboxes', ...(method === 'POST' ? { data: {} } : {}) });
        }
      }
      // HTTP absence checks cover owners AND invited members of both orgs. For
      // foreign writes, only B acts; A always re-reads in finally, even on errors.
      const actors = leg.id === 'L3' ? [B] : [A, scopes.member, B, ...scopes.orgBMembers];
      for (const [actorIndex, actor] of actors.entries()) for (const [index, attempt] of attempts.entries()) {
        let status: number | null = null;
        try {
          // APIRequestContext retains HTML 404s; scopes.send is JSON-only.
          const response = await request.fetch(`${E2E.apiUrl}${attempt.path}`, {
            method: attempt.method, data: attempt.data, headers: scopes.headers(actor), maxRedirects: 0 });
          status = response.status();
        } finally {
          const after = await scopes.send<Table>(A, 'GET', tablePath(dataset), undefined, tableParams);
          expect.soft(after.status).toBe(200);
          expect.soft(fingerprint(after.body.result.table) === baseline,
            `${leg.id} A data unchanged after attempt`).toBe(true);
          expect.soft(fingerprint(await probe.pg(graphQuery, [dataset, A.organizationId])) === graphBefore,
            `${leg.id} A PG graph unchanged after attempt`).toBe(true);
          await testInfo.attach(`j03-${leg.id}-attempt-${actorIndex}-${index}`, { contentType: 'application/json',
            body: JSON.stringify({ status, unchanged: fingerprint(after.body.result.table) === baseline }) });
        }
        if (leg.id === 'L3') expect.soft(status === 403 || status === 404, 'L3 foreign grid write refused').toBe(true);
        else expect.soft(status, `${leg.id} absent API route must be 404`).toBe(404);
      }
      expect(testInfo.errors.length, `${leg.id} executed assertions`).toBe(0);
      // Partial executable evidence never promotes a whole release leg.
      test.skip(true, leg.gap);
    };
    // Stock absence rehearsal remains API-only: a missing browser installation
    // must not suppress its historical assertions. This selects fixtures, NOT
    // applicability; an unexpectedly admitted LM still BLOCKS without an engine.
    if (leg.id === 'L5' && !(gatewayProfile() === 'stock' && process.env.E2E_STACK === 'standalone')) test(`${id}: J03.${leg.id} ${leg.title}`, details,
      async ({ scopeActors, scopeProbe, request, coreRuntime }, info) => body({ scopeActors, scopeProbe, request }, info, coreRuntime));
    else test(`${id}: J03.${leg.id} ${leg.title}`, details,
      async ({ scopeActors, scopeProbe, request }, info) => body({ scopeActors, scopeProbe, request }, info));
    continue;
  }

    test(`${id}: J03.L1 ${leg.title}`, details, async ({ scopeActors: scopes, scopeProbe: probe, walk }, testInfo) => {
      test.setTimeout(120_000);
      const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
      const value = `e2e-j03-a-${suffix}`;
      const replacement = `e2e-j03-b-${suffix}`;
      const A = scopes.ownerA;
      const B = scopes.ownerB;
      const created = await scopes.send<Created>(A, 'POST', CREATE_DATASET,
        { dataset_name: `e2e-j03-${suffix}`, number_of_rows: 1, number_of_columns: 1 });
      expect(created.status, 'A public dataset seed').toBe(200);
      const dataset = created.body.result.dataset_id;
      const rows = await probe.pg<Cell>(
        `SELECT c.id::text, c.dataset_id::text, c.row_id::text, c.column_id::text, c.value
         FROM model_hub_cell c JOIN model_hub_dataset d ON d.id = c.dataset_id
         WHERE c.dataset_id = $1 AND d.organization_id = $2 AND NOT c.deleted ORDER BY c.id`,
        [dataset, A.organizationId]);
      expect(rows.length, 'one owned cell').toBe(1);
      const cell = rows[0];
      const written = await scopes.send(A, 'POST', cellPath(dataset),
        { row_id: cell.row_id, column_id: cell.column_id, new_value: value });
      expect(written.status, 'authorized positive-control write').toBe(200);
      const before = await scopes.send<Table>(A, 'GET', tablePath(dataset), undefined, tableParams);
      expect(before.status, 'A authenticated baseline read').toBe(200);
      expect(before.body.result.table.length).toBe(1);
      const baselineCell = before.body.result.table[0][cell.column_id] as { cell_id: string; cell_value: string };
      expect(baselineCell.cell_id === cell.id && baselineCell.cell_value === value,
        'A reader sees the seeded cell, not an empty or stale response').toBe(true);
      const baseline = fingerprint(before.body.result.table);
      await testInfo.attach('j03-l1-identities', { contentType: 'application/json', body: JSON.stringify({
        orgA: fingerprint(A.organizationId), orgB: fingerprint(B.organizationId),
        dataset: fingerprint(dataset), cell: fingerprint(cell.id), row: fingerprint(cell.row_id),
        column: fingerprint(cell.column_id), baseline,
      }) });

      const context = await walk.open(A, 'j03-l1-owner-a');
      const page = await context.newPage();
      await page.goto(`/dashboard/develop/${dataset}`, { waitUntil: 'domcontentloaded' });
      await expect.poll(() => page.getByText(value, { exact: true }).isVisible(),
        { timeout: UI_READY }).toBe(true);

      // Distinguish the direct lookup from the legacy fallback lookup. A B-owned
      // URL parent must not authorize A's row/column. This does NOT substitute
      // for the actual NULL Cell.dataset fixture requested in HR-JRN-09.
      const ownB = await scopes.send<Created>(B, 'POST', CREATE_DATASET,
        { dataset_name: `e2e-j03-b-parent-${suffix}`, number_of_rows: 1, number_of_columns: 1 });
      expect(ownB.status).toBe(200);
      for (const [attempt, parent] of [dataset, ownB.body.result.dataset_id].entries()) {
        let status: number | null = null;
        try {
          status = (await scopes.send(B, 'POST', cellPath(parent),
            { row_id: cell.row_id, column_id: cell.column_id, new_value: replacement })).status;
        } finally {
          // The A read MUST run even for a successful attack or transport error.
          // Do not assert denial first and short-circuit this critical check.
          const after = await scopes.send<Table>(A, 'GET', tablePath(dataset), undefined, tableParams);
          expect.soft(after.status, `A backend read after B attempt ${attempt}`).toBe(200);
          expect.soft(fingerprint(after.body.result?.table) === baseline,
            `A backend data unchanged after B attempt ${attempt}`).toBe(true);
          const stored = await probe.pg<{ value: string }>(
            `SELECT c.value FROM model_hub_cell c JOIN model_hub_row r ON r.id = c.row_id
             JOIN model_hub_dataset d ON d.id = r.dataset_id
             WHERE c.id = $1 AND d.organization_id = $2`, [cell.id, A.organizationId]);
          expect.soft(stored.length === 1 && stored[0].value === value,
            `A PG cell unchanged after B attempt ${attempt}`).toBe(true);
          await testInfo.attach(`j03-l1-attempt-${attempt}`, { contentType: 'application/json',
            body: JSON.stringify({ status, unchanged: fingerprint(after.body.result?.table) === baseline }) });
        }
        expect.soft(status === 403 || status === 404, `B attempt ${attempt} must be refused`).toBe(true);
      }
      // A product failure stays a failure; never convert soft failures into the
      // legacy-fixture skip. On dev 783e3d3173 this ordinary-cell check is expected RED.
      expect(testInfo.errors.length, 'all ordinary-cell isolation assertions').toBe(0);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect.poll(() => page.getByText(value, { exact: true }).isVisible(),
        { timeout: UI_READY }).toBe(true);
      await expect.poll(() => page.getByText(replacement, { exact: true }).count(), POLL.ASYNC_JOB).toBe(0);
    });
}

// Kept separate so an unseedable historical state never masks ordinary-cell
// failures or changes their green outcome into SKIPPED. J03 still owes this case.
test(`${FLOW}.L1-legacy: J03.L1 legacy NULL dataset subcase`, {
  tag: ['@flow', '@journey', '@journey-J03'],
  annotation: [flowAnnotation({ id: `${FLOW}.L1-legacy`, area: 'journeys',
    userGoal: 'BLOCKED subcase: legacy NULL dataset cells retain parent authorization',
    steps: ['Seed a legacy NULL-dataset cell without changing its row/column parents'],
    backendChecks: [LEGACY_GAP] }), { type: 'journey-flow-id', description: FLOW }],
}, async ({}) => {
  test.setTimeout(120_000);
  test.skip(true, LEGACY_GAP);
});
