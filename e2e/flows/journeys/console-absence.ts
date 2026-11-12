import type { APIRequestContext, TestInfo } from '@playwright/test';
import { test, expect, type Walk } from '../../lib/walk/fixtures';
import type { ScopeActors, ScopeActor } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { gatewayProfile } from '../../lib/profile';
import { flowAnnotation } from '../../lib/flow-meta';
import { chromiumStrictArgs, FIREFOX_STRICT_PREFS } from '../../lib/walk/browsers';
import { lm708Binding } from './console-core-seams';

// Domain assertions only: no new fixture, actor, client, profile or stack here.
// JOURNEYS-708 v2 J16-J19: every leg requires tuple:T-FULL & admitted:console.
// The current harness has no LM-708 reader or Console admission seam. A fresh
// non-admitted org makes that conjunction false independently of the tuple.
// This executable denial rehearsal never qualifies a positive capability gate.
export interface ConsoleLeg {
  id: string;
  title: string;
  applies_when: string;
  flags_required: readonly string[];
  steps: readonly string[];
  pass_criteria: string;
  gap: string;
  // Paths pinned in the individual specs; GET only until no-dispatch attestation
  // and a host-assertion client exist. A synthetic child ID is NOT isolation proof.
  reads: readonly string[];
  model?: boolean;
}

// H11 uses these same settings in playwright.walk.config.ts. That runner only
// selects flows/walks, so consume its options under the existing root config.
// Each invocation selects ONE browser with the CLI --browser flag (the exact
// packets/J16/CONSUMER-core.json argv); neither shared config is modified. This
// file never names an engine: test.use() outranks the project use that --browser
// sets, so a browserName here would silently replace the selected engine.
// E2E_CONSOLE_BROWSER is retired as a selector; when set it is only a cross-check
// that must name the selected engine (consoleEngine, console-core-seams browserGate).
// JOURNEYS-708 core legs require Chromium, Firefox AND WebKit. WebKit has no H11
// strict profile (same shape as gw-hr-cj walk/browsers.ts consoleBrowserOptions)
// and Stage A E4 refuses it, so it is selectable only so that every positive
// WebKit cell records BLOCKED (console-core-seams.ts WEBKIT_RUNTIME_QUALIFIED)
// instead of failing to load; a WebKit absence run stays a non-qualifying rehearsal.
const browserSelector = process.env.E2E_CONSOLE_BROWSER ?? null;
if (browserSelector !== null && browserSelector !== 'chromium' && browserSelector !== 'firefox' && browserSelector !== 'webkit') {
  throw new Error('E2E_CONSOLE_BROWSER must be chromium, firefox or webkit');
}
export const CONSOLE_BROWSER_SELECTOR: string | null = browserSelector;
type ConsoleLaunchOptions = { args?: string[]; firefoxUserPrefs?: Record<string, string | number | boolean> };
/** H11 strict settings for the engine the invocation selected (none for WebKit). */
export function consoleLaunchOptions(browserName: string): ConsoleLaunchOptions {
  return browserName === 'firefox' ? { firefoxUserPrefs: { ...FIREFOX_STRICT_PREFS } }
    : browserName === 'chromium' ? { args: chromiumStrictArgs() } : {};
}
export const consoleOptions = {
  // A worker fixture keyed on the selected engine, never a second engine selector.
  launchOptions: async ({ browserName }: { browserName: string },
    use: (options: ConsoleLaunchOptions) => Promise<void>) => use(consoleLaunchOptions(browserName)),
  scopeRoster: { orgB: [3 as const] },
  trace: 'off' as const, screenshot: 'off' as const, video: 'off' as const,
};
/** The engine this test runs in. With no file-level override the project use that
 * --browser sets IS the effective engine; Playwright's own default is chromium
 * (playwright/lib/index.js:192-193 @ 1.62). A disagreeing E2E_CONSOLE_BROWSER is a
 * configuration error, never applied. */
export function consoleEngine(info: TestInfo): string {
  const engine = String(info.project.use.browserName ?? 'chromium');
  if (CONSOLE_BROWSER_SELECTOR !== null && CONSOLE_BROWSER_SELECTOR !== engine) {
    throw new Error(`E2E_CONSOLE_BROWSER disagrees with the --browser selection (${CONSOLE_BROWSER_SELECTOR}->${engine})`);
  }
  return engine;
}

// tfc/capabilities/views.py:CapabilitiesView.get and contracts.py. A failed or
// malformed capabilities read is a failure, never evidence that Console is OFF.
type Capabilities = { features: Record<string, { allowed: boolean; reason_code: string }> };
// ProductGate.jsx and routes/sections/index.jsx both send dark routes to /404.
const UI_READY = 60_000;
export const CORE_ROUTE = '/dashboard/console';
const CAPS = '/api/capabilities/';
const EDGE_CAPS = '/omega/api/workspace/capabilities';
const CONSOLE_ASSETS = '/omega/console/';

// One result shape for absence and positive legs. state is the branch the test
// evaluated: POSITIVE (capability branch, admitted tuple), WITHDRAWN (absence
// branch: denial asserted, capability not applicable) or BLOCKED (not evaluable:
// seam, applicability, tenant, grant, engine or publication gate missing).
// capabilityPassed is COMPUTED: only a POSITIVE, live, positive-arm leg whose
// every required oracle passed with zero test errors can set it. Absence,
// rehearsal, negative/restored controls and blocked legs never can.
export type ConsoleLegState = 'POSITIVE' | 'WITHDRAWN' | 'BLOCKED';
export type ConsoleArm = 'absence' | 'positive' | 'negative' | 'restored';
export type ConsoleOracleOutcome = 'PASS' | 'FAIL' | 'KILLED' | 'NOT_KILLED' | 'REHEARSAL_REFUSED' | 'NOT_RUN';
export interface ConsoleOracleRecord { id: string; outcome: ConsoleOracleOutcome; fault?: string; kind?: string }
export function computeCapabilityPassed(input: { state: ConsoleLegState; arm: ConsoleArm;
  provenance: 'LIVE' | 'REHEARSAL' | null; required: readonly string[];
  oracles: readonly ConsoleOracleRecord[]; errors: number }): boolean {
  if (input.state !== 'POSITIVE' || input.arm !== 'positive' || input.provenance !== 'LIVE') return false;
  if (input.errors !== 0 || input.required.length === 0 || input.oracles.length === 0) return false;
  if (input.oracles.some(o => o.fault !== undefined || o.outcome !== 'PASS')) return false;
  return input.required.every(id => input.oracles.some(o => o.id === id));
}
export function consoleLegResult<T extends Record<string, unknown>>(fields: T & { state: ConsoleLegState;
  stateReason: string | null; arm: ConsoleArm; provenance: 'LIVE' | 'REHEARSAL' | null; lm708: string | null;
  required: readonly string[]; oracles: readonly ConsoleOracleRecord[] }, errors: number) {
  return { ...fields, releaseAccepted: false as const,
    capabilityPassed: computeCapabilityPassed({ state: fields.state, arm: fields.arm, provenance: fields.provenance,
      required: fields.required, oracles: fields.oracles, errors }) };
}

export function consoleAnnotation(journey: string, flow: string, leg: ConsoleLeg) {
  return [
    flowAnnotation({ id: `${flow}.${leg.id}`, area: 'journeys',
      userGoal: `Check access to ${leg.title}; admitted capability execution remains harness-blocked`,
      steps: ['Read the Console admission decision for fresh owners and members in two organizations',
        'For each non-admitted organization, open the Console and probe its public ingress and leg-specific API reads',
        'Read capabilities and rollout storage again; report absence separately from the owed admitted journey'],
      backendChecks: ['Capabilities must be readable and Console must not be allowed for an absence branch',
        'Every checked absent public/API route answers exactly 404, without following redirects',
        'Capabilities and existing Console rollout/access rows are unchanged in both organizations',
        `Not executed on this harness: ${leg.gap}`],
    }),
    { type: 'journey-flow-id', description: flow },
    { type: 'journey-contract', description: JSON.stringify({ journey, ...leg,
      if_absent: 'Execute denial rehearsal, never qualify the capability gate',
      positive_provider: leg.model ? 'harness-profile mock-llm plus receipts; no live-model acceptance' : null }) },
  ];
}

async function capabilities(scopes: ScopeActors, actor: ScopeActor): Promise<Capabilities> {
  const result = await scopes.send<Capabilities>(actor, 'GET', CAPS);
  expect(result.status, 'capabilities positive control').toBe(200);
  expect(Object.keys(result.body.features).length, 'nonempty capability registry').toBeGreaterThan(0);
  for (const decision of Object.values(result.body.features)) expect(typeof decision.allowed).toBe('boolean');
  return result.body;
}

async function rolloutSnapshot(probe: StateProbe, orgs: string[]) {
  // omega_rollout/models.py ProductRollout/ProductAccess. The older standalone
  // product can lack this app. Check table existence explicitly, not catch SQL
  // errors as absence. SELECT-only StateProbe remains the sole storage reader.
  const [tables] = await probe.pg<{ product: string | null; access: string | null }>(
    "SELECT to_regclass('public.omega_rollout_product')::text AS product, " +
    "to_regclass('public.omega_rollout_access')::text AS access");
  const products = tables.product ? await probe.pg(
    'SELECT product, stage, percentage, paused, epoch FROM omega_rollout_product WHERE product = $1', ['console']) : null;
  const access = tables.access ? await probe.pg(
    'SELECT id, product, organization_id, workspace_id, effect, cohort FROM omega_rollout_access ' +
    'WHERE product = $1 AND organization_id = ANY($2::uuid[]) ORDER BY id', ['console', orgs]) : null;
  return { tables, products, access };
}

export async function checkConsoleAbsence(
  journey: string, leg: ConsoleLeg,
  fixtures: { scopeActors: ScopeActors; scopeProbe: StateProbe; request: APIRequestContext; walk: Walk },
  info: TestInfo,
): Promise<void> {
  const { scopeActors: scopes, scopeProbe: probe, request, walk } = fixtures;
  const selectedBrowser = consoleEngine(info);
  // The LM-708 digest when the runner binds one (console-core-seams.ts); null
  // with a value-free reason otherwise. Never an applicability inference.
  const binding = await lm708Binding(leg.applies_when);
  const unevaluable = process.env.E2E_STACK !== 'standalone' || gatewayProfile() !== 'stock';
  if (unevaluable) {
    // Record BLOCKED before the skip below, so this is never a silent skip.
    await info.attach('console-leg-result', { contentType: 'application/json', body: JSON.stringify(consoleLegResult({
      journey, leg: leg.id, browser: selectedBrowser, arm: 'absence', state: 'BLOCKED',
      stateReason: 'HR-CJ-01:composition-not-standalone-stock', status: 'HARNESS-GAP', provenance: null,
      applies_when: leg.applies_when, lm708: binding.digest, lm708Reason: binding.reason,
      required: [], oracles: [], owed: leg.gap }, info.errors.length)) });
  }
  // Applies_when cannot be evaluated on another composition without the LM-708
  // reader. Never infer T-DARK or T-SHELL from an HTTP error or missing env var.
  test.skip(unevaluable,
    'HARNESS-GAP HR-CJ-01: LM-708 tuple/served/admitted/flags reader and Console composition required; only standalone-stock denial rehearsal is executable');
  const actors = [scopes.ownerA, scopes.member, scopes.ownerB, ...scopes.orgBMembers];
  const orgs = [scopes.ownerA.organizationId, scopes.ownerB.organizationId];
  expect(new Set(orgs).size, 'independent organizations').toBe(2);
  expect(scopes.orgBMembers).toHaveLength(1);
  const before = await Promise.all(actors.map(actor => capabilities(scopes, actor)));
  const storageBefore = await rolloutSnapshot(probe, orgs);
  const denied = before.map(caps => caps.features.omega_console?.allowed !== true);
  const observations: { actor: number; origin: string; path: string; status: number }[] = [];
  let status: 'FAIL' | 'ABSENT' | 'HARNESS-GAP' = 'FAIL';
  // Branch, decided before any assertion: every org denied -> absence branch
  // (WITHDRAWN); any admitted org -> its positive is not evaluated here (BLOCKED).
  const state: ConsoleLegState = denied.every(Boolean) ? 'WITHDRAWN' : 'BLOCKED';
  const stateReason = state === 'WITHDRAWN' ? 'absence-branch:non-admitted-orgs'
    : 'POSITIVE_NOT_EVALUATED_IN_ABSENCE_HELPER';
  const result = () => consoleLegResult({ journey, leg: leg.id, browser: selectedBrowser, arm: 'absence', state,
    stateReason, status, provider: 'none (read-only denial)', provenance: null, lm708: binding.digest,
    lm708Reason: binding.reason, required: [], oracles: [] }, info.errors.length);
  walk.receipt.extra.consoleJourney = { journey, leg: leg.id, applies_when: leg.applies_when,
    flags_required: leg.flags_required, environment: 'standalone-stock-rehearsal', lm708: binding.digest,
    provider: 'none (read-only denial)', state, releaseAccepted: false, capabilityPassed: result().capabilityPassed };
  try {
    for (const [i, actor] of actors.entries()) {
      if (!denied[i]) continue; // Run the other org's absence branch before reporting the positive gap.
      const consoleDecision = before[i].features.omega_console;
      if (consoleDecision) expect(consoleDecision.allowed, 'Console admission is false').toBe(false);
      for (const [originName, origin] of [['public', E2E.appUrl], ['api', E2E.apiUrl]]) {
        expect(new URL(origin).hostname, 'isolated harness origin').toBe('localhost');
        for (const path of [EDGE_CAPS, CONSOLE_ASSETS, ...leg.reads]) {
          const response = await request.get(`${origin}${path}`, {
            headers: scopes.headers(actor), maxRedirects: 0, timeout: 15_000,
          });
          observations.push({ actor: i, origin: originName, path, status: response.status() });
          expect.soft(response.status(), `${journey}.${leg.id} absent ${originName} ${path}`).toBe(404);
          await response.dispose();
        }
      }
      const context = await walk.open(actor, `${journey}-${leg.id}-actor-${i}`);
      const page = await context.newPage();
      await page.goto(CORE_ROUTE, { waitUntil: 'domcontentloaded' });
      await expect.soft(page, `${journey}.${leg.id} non-admitted Console UI`).toHaveURL(/\/404\/?$/, { timeout: UI_READY });
      // ConsoleFramePage.jsx only mounts an iframe after ProductGate admits it.
      await expect.soft(page.locator('iframe'), 'non-admitted Console has no embedded daemon').toHaveCount(0);
      await context.close();
    }
    const after = await Promise.all(actors.map(actor => capabilities(scopes, actor)));
    expect(after, 'both orgs retain exact capabilities after denial probes').toEqual(before);
    expect(await rolloutSnapshot(probe, orgs), 'both orgs retain exact rollout storage').toEqual(storageBefore);
    // A soft assertion must never be erased by the explicit positive-path skip.
    expect(info.errors.length, `${journey}.${leg.id} denial assertions`).toBe(0);
    if (denied.some(value => !value)) {
      status = 'HARNESS-GAP';
      test.skip(true, `HARNESS-GAP HR-CJ-01: admitted Console requires LM-708 and host assertion fixtures. ${leg.gap}`);
    }
    status = 'ABSENT';
    info.annotations.push({ type: 'journey-status', description: 'ABSENT (denial rehearsal only; capability not evaluated)' });
  } finally {
    const final = result();
    Object.assign(walk.receipt.extra.consoleJourney as Record<string, unknown>, { capabilityPassed: final.capabilityPassed });
    await info.attach('console-leg-result', { contentType: 'application/json', body: JSON.stringify({
      journey, leg: leg.id, browser: selectedBrowser, status, orgCount: 2, actorCount: actors.length,
      applies_when: leg.applies_when, admitted: denied.map(value => !value),
      observations, rolloutTablesPresent: { product: storageBefore.tables.product !== null,
        access: storageBefore.tables.access !== null },
      state: final.state, stateReason: final.stateReason, arm: final.arm, lm708: final.lm708,
      lm708Reason: binding.reason, releaseAccepted: final.releaseAccepted,
      capabilityPassed: final.capabilityPassed, owed: leg.gap,
    }) });
  }
}
