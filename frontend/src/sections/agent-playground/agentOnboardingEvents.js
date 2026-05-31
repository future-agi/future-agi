import {
  appendSetupQuickStartAttributionToHref,
  setupQuickStartAttributionParams,
} from "src/sections/auth/jwt/setup-org-quick-starts";

const toSearchParams = (search = "") =>
  search instanceof URLSearchParams
    ? search
    : typeof search === "string" && search.startsWith("?")
      ? new URLSearchParams(search.slice(1))
      : new URLSearchParams(search);

const compactObject = (value = {}) =>
  Object.fromEntries(
    Object.entries(value).filter(
      ([, item]) => item !== undefined && item !== null && item !== "",
    ),
  );

export const agentSetupQuickStartAttributionFromSearch = (search = "") => {
  const params = toSearchParams(search);
  return {
    quick_start_goal: params.get("quick_start_goal"),
    quick_start_id: params.get("quick_start_id"),
    quick_start_primary_path: params.get("quick_start_primary_path"),
  };
};

export const appendAgentOnboardingAttributionToHref = (
  href,
  attributionOrSearch = {},
) =>
  appendSetupQuickStartAttributionToHref(
    href,
    attributionOrSearch instanceof URLSearchParams ||
      typeof attributionOrSearch === "string"
      ? agentSetupQuickStartAttributionFromSearch(attributionOrSearch)
      : attributionOrSearch,
  );

const agentQuickStartAttributionInput = ({
  quickStartAttribution,
  search,
} = {}) =>
  quickStartAttribution || agentSetupQuickStartAttributionFromSearch(search);

export const buildAgentBuilderHref = ({
  agentId,
  onboarding = "run-scenario",
  quickStartAttribution,
  search,
  versionId,
} = {}) => {
  if (!agentId) return null;
  const params = new URLSearchParams();
  if (versionId) params.set("version", versionId);
  if (onboarding) params.set("onboarding", onboarding);

  return appendAgentOnboardingAttributionToHref(
    `/dashboard/agents/playground/${agentId}/build?${params.toString()}`,
    agentQuickStartAttributionInput({ quickStartAttribution, search }),
  );
};

export const buildAgentReviewRunHref = ({
  agentId,
  quickStartAttribution,
  search,
  versionId,
} = {}) => {
  if (!agentId) return null;
  const params = new URLSearchParams();
  if (versionId) params.set("version", versionId);
  params.set("onboarding", "review-run");

  return appendAgentOnboardingAttributionToHref(
    `/dashboard/agents/playground/${agentId}/executions?${params.toString()}`,
    agentQuickStartAttributionInput({ quickStartAttribution, search }),
  );
};

export const buildAgentOnboardingReturnHref = ({
  eventName,
  quickStartAttribution,
  search,
  ...attributionInput
} = {}) =>
  appendAgentOnboardingAttributionToHref(
    `/dashboard/home?mode=daily-quality&source=onboarding&target_event=${encodeURIComponent(
      eventName || "agent_eval_created",
    )}`,
    agentQuickStartAttributionInput({
      quickStartAttribution:
        quickStartAttribution ||
        (Object.keys(attributionInput).length ? attributionInput : undefined),
      search,
    }),
  );

export const buildAgentCreatedPayload = ({
  agentId,
  quickStartAttribution,
} = {}) => ({
  eventName: "agent_created",
  primaryPath: "agent",
  stage: "create_agent",
  source: "agent_playground",
  artifactType: "agent",
  artifactId: String(agentId || "agent"),
  metadata: compactObject({
    agent_id: agentId,
  }),
  idempotencyKey: ["agent_created", agentId || "agent"].join(":"),
  isSample: false,
  ...setupQuickStartAttributionParams(quickStartAttribution),
});

export const buildAgentTraceReviewedPayload = ({
  agentId,
  executionId,
  nodeExecutionId,
  quickStartAttribution,
} = {}) => ({
  eventName: "agent_trace_reviewed",
  primaryPath: "agent",
  stage: "review_agent_trace",
  source: "agent_playground",
  artifactType: "graph_execution",
  artifactId: String(executionId || "graph-execution"),
  metadata: compactObject({
    agent_id: agentId,
    graph_execution_id: executionId,
    node_execution_id: nodeExecutionId,
  }),
  idempotencyKey: [
    "agent_trace_reviewed",
    executionId || "graph-execution",
    nodeExecutionId || "node-execution",
  ].join(":"),
  isSample: false,
  ...setupQuickStartAttributionParams(quickStartAttribution),
});
