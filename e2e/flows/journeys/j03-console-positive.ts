// Positive body for the canonical JRN-E2E-003.L5, not a second journey.
// Normative: J03/AUTHORING-CONTRACTS.json:13-39,368-400; PACKET A01-A07.
// Source-bound finite routes/serializers are G13 NOT DELIVERED. This consumer
// never guesses endpoints from these family names or discovers a passing subset.
import type { BrowserContext, TestInfo } from '@playwright/test';
import { evaluateCore, qualifyCore, coreBrowserContext, check, ConsoleCoreBlocked, ConsoleCoreFailure, coreReceipt, requireProducer,
  resolveSeam, sha, verifyAdmission, type Binding, type CoreContext, type CoreRuntime } from './console-core-seams';

type Credential = 'browser-assertion' | 'personal-key';
type Family = 'agent' | 'run' | 'events' | 'cancel' | 'audit';
interface WireCase {
  caseDigest: string; family: Family; method: string; pathTemplate: string;
  sourceContractDigest: string; serializerDigest: string; envelopeDigest: string;
  ownSuccessStatus: number; foreignStatus: 403 | 404; nonmutating: boolean;
}
interface Graph {
  organizationId: string; workspaceId: string; agentId: string; runId: string;
  engineRunId: string; auditIds: string[];
  agent: { id: string; organizationId: string; workspaceId: string; fields: Record<string, unknown> };
  run: { id: string; agentId: string; organizationId: string; workspaceId: string; fields: Record<string, unknown> };
  // Ordered events are deliberately NOT sorted; reordering must fail equality.
  events: { id: string; contentDigest: string }[];
  audit: { id: string; contentDigest: string }[];
}
interface Observation {
  status: number; authenticated: boolean; ownerScopeMatched: boolean;
  foreignPayloadItems: number; applicationFrames: number;
  unauthorizedCancelDispatches: number; executions: number;
  // Verified request authority, not the resource owner or claimed response scope.
  principalDigest: string; organizationDigest: string; credential: Credential;
  assertionDigest: string | null; personalKeyDigest: string | null;
  refusalReason: 'none' | 'tenant-scope' | 'replay' | 'missing-credential' | 'expired' | 'other';
}
/** Test-local consumer port, NOT an existing product/gateway export. G13 must
 * bind each callable to source and accepted wire schema before replacing the
 * requireProducer join. Real HostAssertionClient/MockUpstream are passed through
 * the shared producer, never reimplemented here. All keys/JWTs stay inside it. */
interface IsolationFixture {
  binding: Binding;
  finiteCases: readonly WireCase[];
  // Independently pinned handoff inventory, NOT computed from finiteCases.
  acceptedCaseDigests: readonly string[];
  seedRetained(): Promise<{ A: Graph; B: Graph; heldAtBackendBarrier: boolean;
    personOwnedCredentials: boolean; admittedA: boolean; admittedB: boolean;
    bPrincipalDigest: string; bOrganizationDigest: string; bPersonalKeyDigest: string }>;
  readA(): Promise<Graph>; // Fresh A-authenticated full graph, maxRedirects=0.
  settleAttempt(): Promise<void>; // Backend fence, not sleep or cached reply.
  request(c: WireCase, auth: Credential, target: 'ownB' | 'foreignA'): Promise<Observation>;
  // First request consumes fresh B assertion on own nonmutating endpoint. The
  // second sends EXACTLY the same assertion bytes, without remint/re-auth.
  beginReplay(c: WireCase): Promise<{ first: Observation; repeat(): Promise<Observation> }>;
  retainForL8(): Promise<{ retained: boolean; sameGraph: boolean }>;
  closeTransientClients(): Promise<void>; // Do NOT delete retained L8 resources.
}
const families: readonly Family[] = ['agent', 'run', 'events', 'cancel', 'audit'];
const credentials: readonly Credential[] = ['browser-assertion', 'personal-key'];
const hash = (s: string) => /^[a-f0-9]{64}$/.test(s);
const canonical = (v: unknown): string => {
  if (Array.isArray(v)) return `[${v.map(canonical).join(',')}]`;
  if (v && typeof v === 'object') return `{${Object.entries(v).sort(([a], [b]) =>
    Buffer.compare(Buffer.from(a), Buffer.from(b))).map(([k, x]) => `${JSON.stringify(k)}:${canonical(x)}`).join(',')}}`;
  return JSON.stringify(v);
};
function graphBytes(g: Graph): string {
  check([g.organizationId, g.workspaceId, g.agentId, g.runId, g.engineRunId].every(v => typeof v === 'string' && v.length > 0), 'J03.L5.GRAPH_IDENTITIES');
  check(Object.keys(g.agent.fields).length > 0 && Object.keys(g.run.fields).length > 0 &&
    g.events.length > 0 && g.audit.length > 0 && g.auditIds.length > 0, 'J03.L5.GRAPH_NONEMPTY');
  check(g.agent.id === g.agentId && g.run.id === g.runId && g.run.agentId === g.agentId &&
    [g.agent, g.run].every(r => r.organizationId === g.organizationId && r.workspaceId === g.workspaceId), 'J03.L5.GRAPH_ASSOCIATIONS');
  check(g.audit.length === g.auditIds.length && new Set(g.auditIds).size === g.auditIds.length &&
    new Set(g.audit.map(r => r.id)).size === g.audit.length &&
    g.audit.every(r => g.auditIds.includes(r.id) && hash(r.contentDigest)) &&
    g.events.every(r => !!r.id && hash(r.contentDigest)) && new Set(g.events.map(r => r.id)).size === g.events.length,
  'J03.L5.GRAPH_ASSOCIATIONS');
  return canonical(structuredClone(g));
}
export function foreignRefused(r: Observation, expected: number): boolean {
  return r.authenticated === true && r.status === expected && [403, 404].includes(r.status) &&
    r.foreignPayloadItems === 0 && r.applicationFrames === 0 && r.unauthorizedCancelDispatches === 0 && r.executions === 0;
}
function ownAccepted(r: Observation, c: WireCase): boolean {
  return r.authenticated === true && r.ownerScopeMatched === true && r.status === c.ownSuccessStatus &&
    r.status >= 200 && r.status < 300 && r.executions === 1;
}
function replayRefused(first: Observation, next: Observation): boolean {
  return first.credential === 'browser-assertion' && next.credential === first.credential &&
    hash(first.assertionDigest ?? '') && next.assertionDigest === first.assertionDigest &&
    next.principalDigest === first.principalDigest && next.organizationDigest === first.organizationDigest &&
    next.personalKeyDigest === null && next.refusalReason === 'replay' &&
    [401, 403].includes(next.status) && next.executions === 0 && next.applicationFrames === 0 &&
    next.foreignPayloadItems === 0 && next.unauthorizedCancelDispatches === 0;
}

// Documented test-local planted faults, one per positive oracle. These are NOT
// live qualification receipts. Producer must execute source mutants + restored
// identical oracles per AUTHORING-CONTRACTS.common.control_policy.
export const J03_RED_CONTROLS = Object.freeze({
  binding: 'Wrong one deployed manifest digest or deny intended B admission; ADMISSION_AND_MANIFESTS / A_B_ADMITTED fails.',
  graph: 'Remove run-to-agent/audit association or empty ordered events; GRAPH_ASSOCIATIONS / GRAPH_NONEMPTY fails.',
  own: 'Expire only B auth: ownAccepted fails before foreign-denial evidence can count.',
  foreign: 'For EACH family x BOTH credentials bypass tenant binding; foreignRefused rejects 200 or leaked frame.',
  noDispatch: 'Return nominal 403 while dispatching foreign cancel; unauthorizedCancelDispatches=1 fails.',
  unchanged: 'Change one A agent/run/event/audit byte after refused request; finally equality fails, including transport errors.',
  replay: 'Disable replay store: second same-byte own request executes; replay refusal/count oracle fails.',
  retained: 'Replace protected graph before L8 handoff; sameGraph fails.',
});

// Executable test-local planted observations, never presented as live evidence.
// Real-source mutants/restored runs in J03_RED_CONTROLS remain separately owed.
function refusalFallibility() {
  const green: Observation = { status: 403, authenticated: true, ownerScopeMatched: false,
    foreignPayloadItems: 0, applicationFrames: 0, unauthorizedCancelDispatches: 0, executions: 0,
    principalDigest: sha('synthetic-person'), organizationDigest: sha('synthetic-org'), credential: 'browser-assertion',
    assertionDigest: sha('synthetic-assertion'), personalKeyDigest: null, refusalReason: 'tenant-scope' };
  for (const change of [{ status: 200 }, { status: 401 }, { authenticated: false },
    { foreignPayloadItems: 1 }, { applicationFrames: 1 }, { unauthorizedCancelDispatches: 1 }, { executions: 1 }]) {
    let killed = false;
    try { check(foreignRefused({ ...green, ...change }, 403), 'J03.L5.FOREIGN_REFUSAL'); }
    catch (e) { killed = e instanceof ConsoleCoreFailure && e.assertion === 'J03.L5.FOREIGN_REFUSAL'; }
    check(killed, 'J03.L5.CONTROL_MUST_KILL');
    check(foreignRefused(green, 403), 'J03.L5.CONTROL_RESTORED');
  }
  const repeated: Observation = { ...green, refusalReason: 'replay' };
  check(replayRefused(green, repeated), 'J03.L5.REPLAY_CONTROL_RESTORED');
  for (const change of [{ assertionDigest: null, refusalReason: 'missing-credential' as const, status: 401 },
    { assertionDigest: sha('reminted') }, { executions: 1 }]) {
    check(!replayRefused(green, { ...repeated, ...change }), 'J03.L5.REPLAY_CONTROL_MUST_KILL');
    check(replayRefused(green, repeated), 'J03.L5.REPLAY_CONTROL_RESTORED');
  }
}

export async function runJ03ConsolePositive(info: TestInfo, runtime: CoreRuntime | undefined,
  standaloneStock: boolean): Promise<boolean> {
  let absent = false, applicabilityPending = true;
  await coreReceipt(info, 'J03.L5', async setContext => {
    // Applicability uses ONLY LM seams and stack binding. Positive clients,
    // route inventory and graph construction must not gate the legacy absence arm.
    await resolveSeam('readLM708'); await resolveSeam('bindLM708'); await resolveSeam('evaluateApplicability');
    const binding = requireProducer<Binding>('STAGE_B_BINDING');
    const context = await evaluateCore(binding, 'served:omega-edge & served:ee-daemon & admitted:console');
    setContext(context); applicabilityPending = false;
    if (context.branch === 'absence') {
      absent = true;
      // Original HTTP absence subset below still runs, but remains partial and
      // cannot emit a positive core receipt. Executed J00.L3/claim join is owed.
      throw new ConsoleCoreBlocked('WITHDRAWAL_NOT_CORE');
    }
    refusalFallibility();
    if (!runtime) throw new ConsoleCoreBlocked('BROWSER_EXECUTION');
    await qualifyCore(context, info, runtime);
    await resolveSeam('HostAssertionClient'); await resolveSeam('MockUpstream');
    await verifyAdmission(context); // Before any retained B session/key/run is created.
    const create = requireProducer<(context: CoreContext, browser: BrowserContext) => Promise<IsolationFixture>>('J03_FINITE_ROUTES_AND_GRAPH');
    const fixture = await create(context, coreBrowserContext(context));
    check(fixture.binding === binding, 'J03.L5.FIXTURE_BINDING');
    const table = structuredClone(fixture.finiteCases);
    check(table.length >= families.length && fixture.acceptedCaseDigests.length === table.length &&
      new Set(fixture.acceptedCaseDigests).size === table.length && new Set(table.map(c => c.caseDigest)).size === table.length &&
      table.every(c => hash(c.caseDigest) && fixture.acceptedCaseDigests.includes(c.caseDigest) &&
        hash(c.sourceContractDigest) && hash(c.serializerDigest) && hash(c.envelopeDigest) &&
        families.includes(c.family) && !!c.method && !!c.pathTemplate && [403, 404].includes(c.foreignStatus)) &&
      families.every(f => table.some(c => c.family === f)), 'J03.L5.FINITE_ROUTE_MATRIX');
    let assertions = 2;
    try {
      const seed = await fixture.seedRetained();
      check(seed.heldAtBackendBarrier && seed.personOwnedCredentials && seed.admittedA && seed.admittedB,
        'J03.L5.A_B_ADMITTED');
      check(seed.A.organizationId !== seed.B.organizationId && seed.A.runId !== seed.B.runId &&
        seed.A.agentId !== seed.B.agentId, 'J03.L5.TWO_TENANTS');
      const baseline = graphBytes(seed.A); graphBytes(seed.B);
      check(graphBytes(await fixture.readA()) === baseline, 'J03.L5.FRESH_BASELINE');
      assertions += 4;
      // EVERY operation gets a fresh A read even when request/settlement fails.
      const attempt = async <T>(operation: () => Promise<T>): Promise<T> => {
        try { return await operation(); }
        finally {
          try { await fixture.settleAttempt(); }
          finally {
            const current = graphBytes(await fixture.readA());
            const unchanged = current === baseline;
            await info.attach('j03-l5-graph-check', { contentType: 'application/json',
              body: JSON.stringify({ lm708Digest: context.manifest.digest, unchanged,
                baselineDigest: sha(baseline), finalDigest: sha(current) }) });
            check(unchanged, 'J03.L5.A_UNCHANGED'); assertions++;
          }
        }
      };
      check(hash(seed.bPrincipalDigest) && seed.bOrganizationDigest === sha(seed.B.organizationId) &&
        hash(seed.bPersonalKeyDigest), 'J03.L5.B_CREDENTIAL_BINDING');
      const seenAssertions = new Set<string>();
      const bindRequest = (r: Observation, auth: Credential) => {
        check(r.credential === auth && r.principalDigest === seed.bPrincipalDigest &&
          r.organizationDigest === seed.bOrganizationDigest, 'J03.L5.SAME_B_PRINCIPAL');
        if (auth === 'browser-assertion') {
          check(hash(r.assertionDigest ?? '') && !seenAssertions.has(r.assertionDigest!) &&
            r.personalKeyDigest === null, 'J03.L5.FRESH_ASSERTION');
          seenAssertions.add(r.assertionDigest!);
        } else check(r.personalKeyDigest === seed.bPersonalKeyDigest && r.assertionDigest === null, 'J03.L5.SAME_B_KEY');
        assertions++;
      };
      for (const auth of credentials) for (const c of table) {
        // Consumer checks fresh assertion for EACH ordinary browser request.
        // An own cancel control uses a separate disposable B-owned run per row.
        const own = await attempt(() => fixture.request(c, auth, 'ownB'));
        bindRequest(own, auth);
        check(ownAccepted(own, c), 'J03.L5.OWN_POSITIVE'); assertions++;
        const foreign = await attempt(() => fixture.request(c, auth, 'foreignA'));
        bindRequest(foreign, auth);
        check(foreignRefused(foreign, c.foreignStatus), 'J03.L5.FOREIGN_REFUSAL'); assertions++;
        await info.attach('j03-l5-matrix-cell', { contentType: 'application/json', body: JSON.stringify({
          lm708Digest: context.manifest.digest, family: c.family, credential: auth,
          caseDigest: c.caseDigest, status: foreign.status, executions: foreign.executions,
          cancelDispatches: foreign.unauthorizedCancelDispatches, ownAccepted: ownAccepted(own, c),
          foreignRefused: foreignRefused(foreign, c.foreignStatus),
        }) });
      }
      const replayCase = table.find(c => c.nonmutating);
      check(!!replayCase, 'J03.L5.REPLAY_CASE');
      const replay = await attempt(() => fixture.beginReplay(replayCase));
      bindRequest(replay.first, 'browser-assertion');
      check(ownAccepted(replay.first, replayCase), 'J03.L5.REPLAY_FIRST_USE'); assertions++;
      // Canonical 'replay it twice': both repeats preserve the original bytes.
      // A header-less 401, remint, or unrelated refusal is not replay evidence.
      for (const ordinal of ['SECOND', 'THIRD']) {
        const repeated = await attempt(() => replay.repeat());
        check(replayRefused(replay.first, repeated), `J03.L5.REPLAY_${ordinal}_USE`); assertions++;
      }
      const retention = await fixture.retainForL8();
      check(retention.retained && retention.sameGraph, 'J03.L5.L8_RETAINED'); assertions++;
    } finally { await fixture.closeTransientClients(); }
    return assertions;
  }).catch(error => {
    if (absent && error instanceof ConsoleCoreBlocked && error.seam === 'WITHDRAWAL_NOT_CORE') return;
    // Missing LM composition is BLOCKED, NOT evaluated absence. Still run the
    // standalone-stock historical 404 rehearsal, whose original final skip
    // remains intact. Never swallow positive-client/graph/capture failures.
    if (standaloneStock && applicabilityPending && error instanceof ConsoleCoreBlocked &&
      ['readLM708', 'bindLM708', 'evaluateApplicability', 'STAGE_B_BINDING', 'APPLICABILITY_EVIDENCE'].includes(error.seam)) {
      absent = true; return;
    }
    throw error;
  });
  return !absent;
}
