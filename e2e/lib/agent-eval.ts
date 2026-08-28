import { test } from './fixtures';
import type { DeploymentMode } from './deployment';

// Gates the ONE agent flow that genuinely cannot run off-cloud.
//
// Agent evals themselves run fine on both stacks, contrary to what this file
// used to claim: `agentic_eval` is not `oss_locked`, so the Agents tab is
// unlocked in either mode, and `_run_agent`'s transport resolves either way —
// on EE the `falcon_ai` entitlement selects the managed lane (its activation
// exchange is stubbed at mock-llm, see stack/docker-compose.e2e.yml), and on
// OSS the denial falls through to a direct provider reading FALCON_AI_* env,
// which the same overlay points at agentcc-gateway. Both land on mock-llm.
// EVAL-E2E-025/026/027 are ungated on the strength of that and pass in OSS.
//
// What does NOT survive the OSS denial is the MCP connector API itself:
// POST /falcon-ai/mcp-connectors/ answers 402 ENTITLEMENT_DENIED
// `{"feature": ["falcon_ai"]}` — "This feature requires an EE license key".
// `falcon_ai` is one of only four `oss_locked` features
// (futureagi/tfc/capabilities/registry.py:81-83), so this is a licensing
// decision with nothing behind it to stub: there is no connector to create
// and no entitlement to fake. Hence a skip rather than a mock.
//
// The log line is the debugging handle: `deploymentMode` is worker-scoped, so
// when a skip does not fire this prints the mode the suite actually resolved.
export function skipAgentFlowUnlessEntitled(deploymentMode: DeploymentMode): void {
  const info = test.info();
  const willSkip = deploymentMode === 'oss';

  info.annotations.push({ type: 'deployment-mode', description: deploymentMode });
  info.annotations.push({
    type: 'agent-eval-gate',
    description: willSkip ? 'SKIP — MCP connectors need the EE-only falcon_ai entitlement' : 'RUN',
  });
  // eslint-disable-next-line no-console
  console.log(
    `[agent-eval-gate] ${info.title}\n`
    + `  worker=${info.workerIndex} deploymentMode="${deploymentMode}" -> ${willSkip ? 'SKIP' : 'RUN'}`,
  );

  test.skip(willSkip,
    `MCP connectors need the falcon_ai entitlement, which is oss_locked — `
    + `POST /falcon-ai/mcp-connectors/ answers 402 ENTITLEMENT_DENIED; `
    + `GET /api/deployment-info/ reported mode="${deploymentMode}"`);
}
