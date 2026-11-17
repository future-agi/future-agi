import { apiPath, isContractedApiPath } from 'src/api/contracts/api-surface';

// Kept inside this feature's ownership boundary. Like utils/axios endpoints,
// every path is checked against the generated backend contract, lazily at use.
export const endpoints = {
  list: () => apiPath('/model-hub/skills/'),
  templates: () => apiPath('/model-hub/skills/templates/'),
  checks: () => apiPath('/model-hub/skills/checks/'),
  publish: () => apiPath('/model-hub/skills/publish/'),
  resolve: () => apiPath('/model-hub/skills/resolve/'),
  download: () => apiPath('/model-hub/skills/download/'),
  importPreview: () => apiPath('/model-hub/skills/import/preview/'),
  import: () => apiPath('/model-hub/skills/import/'),
  detail: (skill_id) => apiPath('/model-hub/skills/{skill_id}/', { skill_id }),
  versions: (skill_id) => apiPath('/model-hub/skills/{skill_id}/versions/', { skill_id }),
  version: (skill_id, number) => apiPath('/model-hub/skills/{skill_id}/versions/{number}/', { skill_id, number }),
  file: (skill_id, number) => apiPath('/model-hub/skills/{skill_id}/versions/{number}/files/', { skill_id, number }),
  versionDownload: (skill_id, number) => apiPath('/model-hub/skills/{skill_id}/versions/{number}/download/', { skill_id, number }),
  restore: (skill_id, number) => apiPath('/model-hub/skills/{skill_id}/versions/{number}/restore/', { skill_id, number }),
  compare: (skill_id) => apiPath('/model-hub/skills/{skill_id}/compare/', { skill_id }),
  labels: (skill_id) => apiPath('/model-hub/skills/{skill_id}/labels/', { skill_id }),
  label: (skill_id, label) => apiPath('/model-hub/skills/{skill_id}/labels/{label}/', { skill_id, label }),
  labelHistory: (skill_id) => apiPath('/model-hub/skills/{skill_id}/labels/history/', { skill_id }),
  draft: (skill_id) => apiPath('/model-hub/skills/{skill_id}/draft/', { skill_id }),
  publishDraft: (skill_id) => apiPath('/model-hub/skills/{skill_id}/draft/publish/', { skill_id }),
  usage: (skill_id) => apiPath('/model-hub/skills/{skill_id}/usage/', { skill_id }),
  testingOptions: () => apiPath('/model-hub/skills/testing-options/'),
  testSuite: (skill_id) => apiPath('/model-hub/skills/{skill_id}/tests/', { skill_id }),
  testRunPreview: (skill_id) => apiPath('/model-hub/skills/{skill_id}/test-runs/preview/', { skill_id }),
  testRuns: (skill_id) => apiPath('/model-hub/skills/{skill_id}/test-runs/', { skill_id }),
  testRun: (skill_id, run_id) => apiPath('/model-hub/skills/{skill_id}/test-runs/{run_id}/', { skill_id, run_id }),
  testResults: (skill_id, run_id) => apiPath('/model-hub/skills/{skill_id}/test-runs/{run_id}/results/', { skill_id, run_id }),
  testAttempt: (skill_id, run_id, attempt_id) => apiPath('/model-hub/skills/{skill_id}/test-runs/{run_id}/attempts/{attempt_id}/', { skill_id, run_id, attempt_id }),
  testFiles: (skill_id, run_id) => apiPath('/model-hub/skills/{skill_id}/test-runs/{run_id}/files/', { skill_id, run_id }),
  cancelTestRun: (skill_id, run_id) => apiPath('/model-hub/skills/{skill_id}/test-runs/{run_id}/cancel/', { skill_id, run_id }),
  shareTestRun: (skill_id, run_id) => apiPath('/model-hub/skills/{skill_id}/test-runs/{run_id}/share/', { skill_id, run_id }),
  testEvidence: (skill_id) => apiPath('/model-hub/skills/{skill_id}/test-evidence/', { skill_id }),
  assistPreview: (skill_id) => apiPath('/model-hub/skills/{skill_id}/assist/preview/', { skill_id }),
  assist: (skill_id) => apiPath('/model-hub/skills/{skill_id}/assist/', { skill_id }),
  assistProposal: (skill_id, proposal_id) => apiPath('/model-hub/skills/{skill_id}/assist/{proposal_id}/', { skill_id, proposal_id }),
  applyAssist: (skill_id, proposal_id) => apiPath('/model-hub/skills/{skill_id}/assist/{proposal_id}/apply/', { skill_id, proposal_id }),
  cancelAssist: (skill_id, proposal_id) => apiPath('/model-hub/skills/{skill_id}/assist/{proposal_id}/cancel/', { skill_id, proposal_id }),
  githubPreviews: () => apiPath('/model-hub/skills/github/previews/'),
  githubPreview: (preview_id) => apiPath('/model-hub/skills/github/previews/{preview_id}/', { preview_id }),
  githubImport: () => apiPath('/model-hub/skills/github/import/'),
  source: (skill_id) => apiPath('/model-hub/skills/{skill_id}/source/', { skill_id }),
  checkSource: (skill_id) => apiPath('/model-hub/skills/{skill_id}/source/check-updates/', { skill_id }),
  stageSourceUpdate: (skill_id) => apiPath('/model-hub/skills/{skill_id}/source/stage-update/', { skill_id }),
  labelProtection: (skill_id) => apiPath('/model-hub/skills/{skill_id}/label-protection/', { skill_id }),
  labelProtectionHistory: (skill_id) => apiPath('/model-hub/skills/{skill_id}/label-protection/history/', { skill_id }),
  adoption: (skill_id) => apiPath('/model-hub/skills/{skill_id}/adoption/', { skill_id }),
};

// Skill-trace runtime reads (SKILL-TRACES §6.6 endpoints 1–5 and 8–10, lane
// ST10). The load projection is mounted at the shared /loads/ resource.
// Loads and filter values are mounted. Usage and comparison remain provisional until their shared
// API projections are mounted (SKILLS-API-REVIEW.md); do not enable the feature
// or replace these guards with uncontracted network calls. Hooks in runtime.js
// call `runtimeEndpoint(name, params)`.
//
// A template is used only once the generated contract has it. Until then the
// read fails with the typed code `runtime_route_pending` (the screens show
// their error state), so nothing calls a route the backend does not serve.
export const RUNTIME_ROUTES = Object.freeze({
  skillUsage: '/model-hub/skills/{skill_id}/runtime/', // 1: Loaded by, summary, diagnosis
  skillSeries: '/model-hub/skills/{skill_id}/runtime/timeseries/', // 2
  skillComparison: '/model-hub/skills/{skill_id}/runtime/label-move/', // 3: move card and header chip
  contextUsage: '/model-hub/skills/contexts/{context_id}/runtime-skills/', // 4
  filterValues: '/model-hub/skills/filter-values/', // Shared, cursor-paged Observe pickers
  agentUsage: '/model-hub/skills/runtime/agent/', // 8: agent panel
  contextComparison: '/model-hub/skills/contexts/{context_id}/runtime/release-move/', // 9
  // One mounted load projection serves both resource kinds.
  skillLatestLoad: '/model-hub/skills/loads/',
  contextLatestLoad: '/model-hub/skills/loads/',
});

export const RUNTIME_ROUTE_PENDING = 'runtime_route_pending';

/** Resolve one runtime read. Throws a typed error until the route is contracted. */
export function runtimeEndpoint(name, params = {}) {
  const template = RUNTIME_ROUTES[name];
  if (!template) throw new Error(`Unknown Skills runtime route: ${name}`);
  if (!isContractedApiPath(template)) {
    const error = new Error('This runtime read is not available on this server yet.');
    error.code = RUNTIME_ROUTE_PENDING;
    throw error;
  }
  return template.replace(/\{([^}]+)\}/g, (_, key) => {
    const value = params[key];
    if (value === undefined || value === null || value === '') throw new Error(`Missing runtime path param "${key}"`);
    return encodeURIComponent(String(value));
  });
}
