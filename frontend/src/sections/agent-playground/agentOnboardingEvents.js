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

export const AGENT_ONBOARDING_STARTER_MODEL = "gpt-4o-mini";

export const buildAgentOnboardingStarterPromptConfig = ({
  model = AGENT_ONBOARDING_STARTER_MODEL,
} = {}) => ({
  outputFormat: "string",
  templateFormat: "mustache",
  modelConfig: {
    model,
    modelDetail: {
      modelName: model,
      logoUrl: "",
      providers: "openai",
      isAvailable: true,
    },
    responseFormat: "text",
    responseSchema: null,
    toolChoice: "auto",
    tools: [],
  },
  messages: [
    {
      id: "agent-onboarding-system",
      role: "system",
      content: [
        {
          type: "text",
          text: "You triage AI product issues. Return the likely cause, one check to run, and the next action.",
        },
      ],
    },
    {
      id: "agent-onboarding-user",
      role: "user",
      content: [
        {
          type: "text",
          text: "A user says the AI assistant answered with outdated pricing after a release. Help the team decide what to inspect first.",
        },
      ],
    },
  ],
});

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
  journeyStep,
  quickStartAttribution,
  search,
  tourAnchor,
  versionId,
} = {}) => {
  if (!agentId) return null;
  const params = new URLSearchParams();
  if (versionId) params.set("version", versionId);
  if (onboarding) params.set("onboarding", onboarding);
  if (tourAnchor) params.set("tour_anchor", tourAnchor);
  if (journeyStep) params.set("journey_step", journeyStep);

  return appendAgentOnboardingAttributionToHref(
    `/dashboard/agents/playground/${agentId}/build?${params.toString()}`,
    agentQuickStartAttributionInput({ quickStartAttribution, search }),
  );
};

export const buildAgentEvalBuilderHref = ({
  agentId,
  quickStartAttribution,
  search,
  versionId,
} = {}) =>
  buildAgentBuilderHref({
    agentId,
    journeyStep: "save_agent_eval",
    onboarding: "add-eval",
    quickStartAttribution,
    search,
    tourAnchor: "agent_save_eval_button",
    versionId,
  });

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

export const buildAgentNodeAddedPayload = ({
  agentId,
  nodeId,
  quickStartAttribution,
  versionId,
} = {}) => ({
  eventName: "agent_node_added",
  primaryPath: "agent",
  stage: "add_agent_node",
  source: "agent_playground",
  artifactType: "agent_node",
  artifactId: String(nodeId || "agent-node"),
  metadata: compactObject({
    agent_id: agentId,
    node_id: nodeId,
    version_id: versionId,
  }),
  idempotencyKey: [
    "agent_node_added",
    agentId || "agent",
    nodeId || "agent-node",
  ].join(":"),
  isSample: false,
  ...setupQuickStartAttributionParams(quickStartAttribution),
});

export const buildAgentPrototypeRunCompletedPayload = ({
  agentId,
  executionId,
  quickStartAttribution,
  status,
  versionId,
} = {}) => ({
  eventName: "agent_prototype_run_completed",
  primaryPath: "agent",
  stage: "run_agent_scenario",
  source: "agent_playground",
  artifactType: "graph_execution",
  artifactId: String(executionId || "graph-execution"),
  metadata: compactObject({
    agent_id: agentId,
    graph_execution_id: executionId,
    status,
    version_id: versionId,
  }),
  idempotencyKey: [
    "agent_prototype_run_completed",
    executionId || "graph-execution",
  ].join(":"),
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

export const buildAgentScenarioSavedAsEvalPayload = ({
  agentId,
  nodeId,
  quickStartAttribution,
  versionId,
} = {}) => ({
  eventName: "agent_scenario_saved_as_eval",
  primaryPath: "agent",
  stage: "save_agent_eval",
  source: "agent_playground",
  artifactType: "agent_eval_node",
  artifactId: String(nodeId || "agent-eval-node"),
  metadata: compactObject({
    agent_id: agentId,
    eval_node_id: nodeId,
    version_id: versionId,
  }),
  idempotencyKey: [
    "agent_scenario_saved_as_eval",
    agentId || "agent",
    nodeId || "agent-eval-node",
  ].join(":"),
  isSample: false,
  ...setupQuickStartAttributionParams(quickStartAttribution),
});

export const buildAgentEvalCreatedPayload = ({
  agentId,
  executionId,
  quickStartAttribution,
  status,
  versionId,
} = {}) => ({
  eventName: "agent_eval_created",
  primaryPath: "agent",
  stage: "agent_create_eval",
  source: "agent_playground",
  artifactType: "agent_eval",
  artifactId: String(executionId || versionId || agentId || "agent-eval"),
  metadata: compactObject({
    agent_id: agentId,
    graph_execution_id: executionId,
    status,
    version_id: versionId,
  }),
  idempotencyKey: [
    "agent_eval_created",
    executionId || versionId || agentId || "agent-eval",
  ].join(":"),
  isSample: false,
  ...setupQuickStartAttributionParams(quickStartAttribution),
});
