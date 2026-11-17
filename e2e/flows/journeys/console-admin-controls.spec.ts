import { test } from '../../lib/walk/fixtures';
import { checkConsoleAbsence, consoleAnnotation, consoleOptions, type ConsoleLeg } from './console-absence';

// JOURNEYS-708 v2 J17; each switch is independent. Only L-A/B/C satisfy C7.
const FLOW = 'JRN-E2E-017';
const APPLIES = 'tuple:T-FULL & admitted:console';
const legs: readonly ConsoleLeg[] = [
  { id: 'L-A', title: 'Stop organization runs and pause automation', applies_when: APPLIES,
    flags_required: ['console_emergency_stop', 'FA PEER_PARTS'],
    steps: ['Admin triggers organization-wide stop across all FA workspaces',
      'Observe cancellation within the heartbeat bound, paused automation and an audit row',
      'Repeat the stop with the same Idempotency-Key'],
    pass_criteria: 'All runs stopped within the bound; same-key HTTP retries reach the route without stale cached results; zero head-only Console failures',
    gap: 'HR-CJ-07: need multi-workspace running Console fixture, stop-operation/audit reader and repeatable idempotency request seam; assert all terminal states and pause persistence, new retry operation receipt, unchanged org B and paired-suite evidence',
    // G02 packages/server/src/managed-auth/console-route-manifest.v1.json.
    reads: ['/omega/api/workspace/kill-switch/status', '/omega/api/workspace/audit'] },
  { id: 'L-B', title: 'Transfer and recover an agent business owner', applies_when: APPLIES,
    flags_required: ['agent_ownership'],
    steps: ['Admin transfers the agent business owner on the real FA host',
      'Recover a departed owner', 'Attempt the same operation as a member'],
    pass_criteria: 'Transfer recorded; departed-owner recovery works; member refused',
    gap: 'HR-CJ-08: need Console agent fixture and departed-owner identity control via the public FA seam; assert owner/version/audit after transfer and recovery, member 403 with unchanged owner, and unchanged org B',
    // G06 uses the workforce agent PATCH, not a separate invented ownership route.
    reads: ['/omega/api/workspace/workforce', '/omega/api/workspace/workforce/sharing-candidates'] },
  { id: 'L-C', title: 'Enforce administrator tool policy before execution', applies_when: APPLIES,
    flags_required: ['OMEGA_WORKSPACE_ADMIN_TOOL_POLICY', 'VITE_OMEGA_ADMIN_TOOL_POLICY'],
    steps: ['FA Workspace Admin configures org/workspace allow/deny and an HTTPS hook in hosted settings',
      'Attempt a denied tool call', 'Make the hook fail and try again'],
    pass_criteria: 'FA org is the policy key; Workspace Admin can configure; denial precedes execution; hook failure closes',
    gap: 'HR-CJ-09: need Workspace-Admin-only roster, admitted policy settings, mock tool-call provider, local HTTPS hook/failure control and invocation receipt reader; compare saved FA org/workspace policies and zero tool executions on deny/hook failure',
    // G08 packages/server/src/managed-auth/console-route-manifest.v1.json.
    reads: ['/omega/api/workspace/tool-policy/workspace', '/omega/api/workspace/tool-policy/organization'], model: true },
  { id: 'L-D', title: 'Export tenant-attributed spans to a customer collector', applies_when: APPLIES,
    flags_required: ['console_otlp'],
    steps: ['Admin configures a customer OTLP destination on the allowlist',
      'Run an agent and inspect arrivals at the public-HTTPS-shaped local collector',
      'Attempt a non-allowlisted destination'],
    pass_criteria: 'Spans arrive with tenant attribution; non-allowlisted destinations refused; extra, not core',
    gap: 'HR-CJ-10: need Console OTLP configuration and a local public-HTTPS collector with arrival receipts; assert persisted destination, exact run/span identity and tenant, rejected non-allowlisted save and zero foreign arrivals',
    // 7C route manifest and customer OpenAPI: observe/settings GET/PUT.
    reads: ['/omega/api/workspace/observe/settings'], model: true },
];

test.use(consoleOptions);
for (const leg of legs) {
  test(`${FLOW}.${leg.id}: J17.${leg.id} ${leg.title}`, {
    tag: ['@flow', '@journey', '@journey-J17', ...(leg.model ? ['@mock-provider'] : [])],
    annotation: consoleAnnotation('J17', FLOW, leg),
  }, async ({ scopeActors, scopeProbe, request, walk }, info) => {
    test.setTimeout(480_000); // 4 x UI_READY + bounded backend reads and fixture setup.
    await checkConsoleAbsence('J17', leg, { scopeActors, scopeProbe, request, walk }, info);
  });
}
