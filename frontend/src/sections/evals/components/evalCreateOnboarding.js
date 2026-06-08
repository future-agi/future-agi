import {
  appendSetupQuickStartAttributionToHref,
  setupQuickStartAttributionParams,
} from "src/sections/auth/jwt/setup-org-quick-starts";

const DEFAULT_ARTIFACT_ID = "eval-onboarding";
const EVAL_REVIEW_ARTIFACT_ID = "eval-review";
const EVAL_FIX_ARTIFACT_ID = "eval-failure-action";
const EVAL_SOURCE_FIX_ARTIFACT_ID = "eval-source-fix-route";
const EVAL_FIX_STEP = "fix-eval-failure";
const EVAL_REVIEW_STEP = "review";
const EVAL_REVIEW_STAGE = "review_eval_failures";
const FIRST_QUALITY_LOOP_EVENT = "first_quality_loop_completed";

export const EVAL_FIX_RERUN_ORIGINS = {
  SCORER_EDIT: "scorer_edit",
  SOURCE_FIX: "source_fix",
};

export const EVAL_REVIEW_ACTIONS = {
  COMPLETE: "complete",
  SCORER_EDIT: "scorer_edit",
  SOURCE_FIX: "source_fix",
};

export const EVAL_CREATE_ONBOARDING_STEPS = {
  DATA: "data",
  SCORER: "scorer",
  RUN: "run",
};

export const EVAL_CREATE_SOURCE_TABS = {
  CUSTOM: "Custom",
  DATASET: "Dataset",
  SIMULATION: "Simulation",
  TRACING: "Tracing",
};

const STEP_TO_STAGE = {
  [EVAL_CREATE_ONBOARDING_STEPS.DATA]: "create_eval_dataset",
  [EVAL_CREATE_ONBOARDING_STEPS.SCORER]: "add_eval_scorer",
  [EVAL_CREATE_ONBOARDING_STEPS.RUN]: "run_eval",
};
const EVAL_JOURNEY_CREATE_STEPS = {
  add_eval_scorer: EVAL_CREATE_ONBOARDING_STEPS.SCORER,
  create_eval_dataset: EVAL_CREATE_ONBOARDING_STEPS.DATA,
  run_eval: EVAL_CREATE_ONBOARDING_STEPS.RUN,
};
const EVAL_JOURNEY_REVIEW_STEPS = {
  eval_next_loop: EVAL_FIX_STEP,
  review_eval_failures: EVAL_REVIEW_STEP,
};
const EVAL_DETAIL_TABS = new Set([
  "details",
  "usage",
  "feedback",
  "ground_truth",
]);

const SOURCE_TYPE_TO_TAB = {
  dataset: EVAL_CREATE_SOURCE_TABS.DATASET,
  simulation: EVAL_CREATE_SOURCE_TABS.SIMULATION,
  trace: EVAL_CREATE_SOURCE_TABS.TRACING,
  trace_project: EVAL_CREATE_SOURCE_TABS.TRACING,
};

const SOURCE_TYPE_LABELS = {
  dataset: "Dataset",
  simulation: "Simulation",
  trace: "Trace",
  trace_project: "Trace project",
};

const SOURCE_TYPE_ARTIFACT_TYPES = {
  dataset: "dataset",
  simulation: "project",
  trace: "trace",
  trace_project: "observe_project",
};

const EVAL_STARTER_SCORER_CODE = `from typing import Any

def to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()

def evaluate(output: Any = None, context: dict = None, **kwargs):
    context = context or {}
    span = context.get("span") or kwargs.get("span_context") or {}
    trace = context.get("trace") or kwargs.get("trace_context") or {}
    candidate_output = (
        to_text(output)
        or to_text(span.get("output"))
        or to_text(trace.get("output"))
    )

    if not candidate_output:
        return {"score": 0.0, "reason": "No model output was found for this run."}
    if len(candidate_output) < 20:
        return {"score": 0.5, "reason": "Output exists but is short enough to review."}
    return {"score": 1.0, "reason": "Output exists and is ready for review."}`;

const STEP_COPY = {
  [EVAL_CREATE_ONBOARDING_STEPS.DATA]: {
    currentStep: "Source",
    description:
      "Choose the examples, simulation, or trace source to test before adding the quality check.",
    title: "Choose what to test",
    steps: [
      { label: "Source", complete: false },
      { label: "Quality check", complete: false },
      { label: "Run", complete: false },
      { label: "Review", complete: false },
    ],
  },
  [EVAL_CREATE_ONBOARDING_STEPS.SCORER]: {
    currentStep: "Quality check",
    description:
      "Start with a safe output-quality check, then save it to run against this source.",
    title: "Add the quality check",
    steps: [
      { label: "Source", complete: true },
      { label: "Quality check", complete: false },
      { label: "Run", complete: false },
      { label: "Review", complete: false },
    ],
  },
  [EVAL_CREATE_ONBOARDING_STEPS.RUN]: {
    currentStep: "Run",
    description:
      "Run the quality check once, then review the first result before moving on.",
    title: "Run the first quality check",
    steps: [
      { label: "Source", complete: true },
      { label: "Quality check", complete: true },
      { label: "Run", complete: false },
      { label: "Review", complete: false },
    ],
  },
};

const TRACE_PROJECT_STEP_COPY = {
  [EVAL_CREATE_ONBOARDING_STEPS.DATA]: {
    currentStep: "Trace source",
    description:
      "The reviewed trace project is selected. Next, add a starter scorer for this source.",
    title: "Use reviewed trace project",
    steps: [
      { label: "Trace source", complete: false },
      { label: "Quality check", complete: false },
      { label: "Run", complete: false },
    ],
  },
  [EVAL_CREATE_ONBOARDING_STEPS.SCORER]: {
    currentStep: "First quality check",
    description:
      "A safe output-quality scorer is loaded for this trace project. Create the quality check, then run it once.",
    title: "Create the first trace quality check",
    steps: [
      { label: "Trace source", complete: true },
      { label: "Quality check", complete: false },
      { label: "Run", complete: false },
    ],
  },
  [EVAL_CREATE_ONBOARDING_STEPS.RUN]: {
    currentStep: "Run quality check",
    description:
      "Run the saved quality check once so the first trace-project result is reviewable.",
    title: "Run quality check on trace project",
    steps: [
      { label: "Trace source", complete: true },
      { label: "Quality check", complete: true },
      { label: "Run", complete: false },
    ],
  },
};

const EVAL_RERUN_COPY = {
  currentStep: "Rerun",
  description:
    "Run the eval again after the source fix, then compare the result.",
  title: "Rerun the eval",
  steps: [
    { label: "Review", complete: true },
    { label: "Fix", complete: true },
    { label: "Rerun", complete: false },
    { label: "Inspect", complete: false },
  ],
};

const TRACE_PROJECT_RERUN_COPY = {
  currentStep: "Rerun",
  description:
    "Run the quality check again after the trace-source fix, then compare the result.",
  title: "Rerun the quality check",
  steps: [
    { label: "Review", complete: true },
    { label: "Fix", complete: true },
    { label: "Rerun", complete: false },
    { label: "Inspect", complete: false },
  ],
};

const EVAL_REVIEW_COPY = {
  currentStep: "Review",
  description:
    "Open the first result. If it failed or looks weak, fix the source that produced it; if the source is right, tune the quality check.",
  title: "Review the first quality result",
  steps: [
    { label: "Source", complete: true },
    { label: "Quality check", complete: true },
    { label: "Run", complete: true },
    { label: "Review", complete: false },
    { label: "Fix or finish", complete: false },
  ],
};

const EVAL_REPAIR_REVIEW_COPY = {
  currentStep: "Review rerun",
  description:
    "This run follows a repair action. If the rerun is healthy, continue to Home. If it still looks weak, fix the source again.",
  sourceSummary: {
    description: "The previous run is linked for repair-loop measurement.",
    label: "Repair rerun complete",
  },
  title: "Review the repair attempt",
  steps: [
    { label: "Review", complete: true },
    { label: "Fix", complete: true },
    { label: "Rerun", complete: true },
    { label: "Inspect", complete: false },
  ],
};

const traceProjectReviewCopy = ({ setupLanguage, setupProvider } = {}) => {
  const setupPackageLabel = observeSetupPackageLabel({
    setupLanguage,
    setupProvider,
  });
  const traceQualityCheckLabel = setupPackageLabel
    ? `${setupPackageLabel} trace quality check`
    : "Trace quality check";

  return {
    currentStep: "Review result",
    description: setupPackageLabel
      ? `Review the first ${setupPackageLabel} quality-check result. A healthy result completes setup; a weak or failed result points back to the trace source.`
      : "Review the first trace quality-check result. A healthy result completes setup; a weak or failed result points back to the trace source.",
    sourceSummary: {
      description:
        "The quality check is tied to the trace project you reviewed during setup.",
      label: `${traceQualityCheckLabel} run`,
    },
    title: "Review trace quality-check result",
    steps: [
      { label: "Trace source", complete: true },
      { label: "Quality check", complete: true },
      { label: "Run", complete: true },
      { label: "Review", complete: false },
    ],
  };
};

const EVAL_SOURCE_FIX_COPY = {
  dataset: {
    description:
      "Update the dataset row or expected output that produced the failed result, then rerun the quality check.",
    title: "Fix the eval source",
  },
  simulation: {
    description:
      "Update the simulation scenario or expected behavior that produced this result, then rerun the quality check.",
    title: "Fix the simulation source",
  },
  trace: {
    description:
      "Review the trace evidence, adjust the workflow that produced it, then rerun the quality check.",
    title: "Fix the trace source",
  },
  trace_project: {
    description:
      "Review the traces or project setup that produced this quality-check result, then rerun the quality check.",
    title: "Fix trace source",
  },
};

const validSteps = new Set(Object.values(EVAL_CREATE_ONBOARDING_STEPS));
const validFixRerunOrigins = new Set(Object.values(EVAL_FIX_RERUN_ORIGINS));
const OBSERVE_SETUP_PROVIDERS = new Set([
  "anthropic",
  "bedrock",
  "langchain",
  "llamaindex",
  "mcp",
  "openai",
  "openai_agents",
]);
const OBSERVE_SETUP_PROVIDER_ALIASES = {
  "llama-index": "llamaindex",
  llama_index: "llamaindex",
  "openai-agents": "openai_agents",
  openaiagents: "openai_agents",
};
const OBSERVE_SETUP_LANGUAGES = new Set(["python", "typescript"]);
const OBSERVE_SETUP_PROVIDER_LABELS = {
  anthropic: "Anthropic",
  bedrock: "Bedrock",
  langchain: "LangChain",
  llamaindex: "LlamaIndex",
  mcp: "MCP",
  openai: "OpenAI",
  openai_agents: "OpenAI Agents",
};
const OBSERVE_SETUP_LANGUAGE_LABELS = {
  python: "Python",
  typescript: "TypeScript",
};

const compactMetadata = (metadata = {}) =>
  Object.fromEntries(
    Object.entries(metadata).filter(
      ([, value]) => value !== undefined && value !== null && value !== "",
    ),
  );

const safeKeyPart = (value, fallback) =>
  String(value || fallback)
    .replace(/[^a-zA-Z0-9_-]/g, "-")
    .slice(0, 56);

const artifactTypeForSource = (sourceType, fallback = "eval") =>
  SOURCE_TYPE_ARTIFACT_TYPES[sourceType] || fallback;

const toSearchParams = (search = "") =>
  search instanceof URLSearchParams
    ? new URLSearchParams(search)
    : new URLSearchParams(search);

export const evalSetupQuickStartAttributionFromSearch = (search = "") => {
  const params = toSearchParams(search);
  return {
    quick_start_goal: params.get("quick_start_goal"),
    quick_start_id: params.get("quick_start_id"),
    quick_start_primary_path: params.get("quick_start_primary_path"),
  };
};

export const appendEvalOnboardingAttributionToHref = (
  href,
  attributionOrSearch = {},
) =>
  appendSetupQuickStartAttributionToHref(
    href,
    attributionOrSearch instanceof URLSearchParams ||
      typeof attributionOrSearch === "string"
      ? evalSetupQuickStartAttributionFromSearch(attributionOrSearch)
      : attributionOrSearch,
  );

const evalQuickStartAttributionInput = ({
  quickStartAttribution,
  search,
} = {}) =>
  quickStartAttribution || evalSetupQuickStartAttributionFromSearch(search);

const normalizeFixRerunOrigin = (value) =>
  validFixRerunOrigins.has(value) ? value : null;

const normalizeSetupValue = (value) =>
  typeof value === "string" ? value.trim().toLowerCase() : "";

const normalizeObserveSetupProvider = (value) => {
  const normalizedValue = normalizeSetupValue(value);
  const canonicalValue =
    OBSERVE_SETUP_PROVIDER_ALIASES[normalizedValue] || normalizedValue;
  return OBSERVE_SETUP_PROVIDERS.has(canonicalValue) ? canonicalValue : null;
};

const normalizeObserveSetupLanguage = (value) => {
  const normalizedValue = normalizeSetupValue(value);
  return OBSERVE_SETUP_LANGUAGES.has(normalizedValue) ? normalizedValue : null;
};

const setupIntentFromSearch = (search = "") => {
  const params = toSearchParams(search);
  return {
    setupLanguage: normalizeObserveSetupLanguage(
      params.get("language") || params.get("lang"),
    ),
    setupProvider: normalizeObserveSetupProvider(
      params.get("provider") ||
        params.get("package") ||
        params.get("instrument"),
    ),
  };
};

const setupIntentInput = ({
  search,
  setupIntent,
  setupLanguage,
  setupProvider,
} = {}) => {
  if (setupIntent) {
    return {
      setupLanguage: normalizeObserveSetupLanguage(
        setupIntent.setupLanguage || setupIntent.setup_language,
      ),
      setupProvider: normalizeObserveSetupProvider(
        setupIntent.setupProvider || setupIntent.setup_provider,
      ),
    };
  }
  const parsedIntent = search ? setupIntentFromSearch(search) : {};
  return {
    setupLanguage: normalizeObserveSetupLanguage(
      setupLanguage || parsedIntent.setupLanguage,
    ),
    setupProvider: normalizeObserveSetupProvider(
      setupProvider || parsedIntent.setupProvider,
    ),
  };
};

const appendSetupIntentParams = (params, options = {}) => {
  const { setupLanguage, setupProvider } = setupIntentInput(options);
  if (setupProvider) params.set("provider", setupProvider);
  if (setupLanguage) params.set("language", setupLanguage);
};

const setupIntentMetadata = (options = {}) => {
  const { setupLanguage, setupProvider } = setupIntentInput(options);
  return {
    setup_language: setupLanguage || undefined,
    setup_provider: setupProvider || undefined,
  };
};

const observeSetupPackageLabel = ({ setupLanguage, setupProvider } = {}) => {
  const providerLabel =
    OBSERVE_SETUP_PROVIDER_LABELS[normalizeObserveSetupProvider(setupProvider)];
  if (!providerLabel) return "";
  const languageLabel =
    OBSERVE_SETUP_LANGUAGE_LABELS[normalizeObserveSetupLanguage(setupLanguage)];
  return [providerLabel, languageLabel].filter(Boolean).join(" ");
};

export const getEvalDetailTabFromSearch = (search = "") => {
  const tab = toSearchParams(search).get("tab");
  return EVAL_DETAIL_TABS.has(tab) ? tab : "details";
};

const appendEvalFixRerunParams = (
  params,
  { previousRunId, rerunFrom } = {},
) => {
  const normalizedRerunFrom = normalizeFixRerunOrigin(rerunFrom);
  if (normalizedRerunFrom) params.set("rerun_from", normalizedRerunFrom);
  if (previousRunId) params.set("previous_run_id", previousRunId);
};

const appendTraceContextParams = (params, { traceId } = {}) => {
  if (traceId) params.set("trace_id", traceId);
};

const traceContextMetadata = ({ traceId } = {}) => ({
  trace_id: traceId || undefined,
});

export const getEvalCreateOnboardingParams = (search = "") => {
  const params = toSearchParams(search);
  const rawStep = params.get("step");
  const journeyStep = EVAL_JOURNEY_CREATE_STEPS[params.get("journey_step")];
  const step =
    (validSteps.has(rawStep) && rawStep) ||
    journeyStep ||
    EVAL_CREATE_ONBOARDING_STEPS.SCORER;

  return {
    isOnboarding: params.get("source") === "onboarding" || Boolean(journeyStep),
    previousRunId: params.get("previous_run_id"),
    rerunFrom: normalizeFixRerunOrigin(params.get("rerun_from")),
    runId: params.get("run_id"),
    ...setupIntentFromSearch(params),
    sourceId: params.get("source_id"),
    sourceType: params.get("source_type"),
    step,
    traceId: params.get("trace_id"),
    tourAnchor: params.get("tour_anchor"),
  };
};

export const shouldAutoConfirmEvalOnboardingSource = ({
  isOnboarding,
  sourceId,
  sourceType,
  step,
} = {}) =>
  Boolean(
    isOnboarding &&
      step === EVAL_CREATE_ONBOARDING_STEPS.DATA &&
      sourceType === "trace_project" &&
      sourceId,
  );

export const shouldAutoSaveEvalOnboardingStarterScorer = ({
  isOnboarding,
  sourceId,
  sourceType,
  step,
} = {}) =>
  Boolean(
    isOnboarding &&
      step === EVAL_CREATE_ONBOARDING_STEPS.SCORER &&
      sourceType === "trace_project" &&
      sourceId,
  );

export const getEvalCreateOnboardingCopy = ({
  rerunFrom,
  setupLanguage,
  setupProvider,
  sourceType,
  step,
} = {}) => {
  if (step === EVAL_CREATE_ONBOARDING_STEPS.RUN && rerunFrom) {
    if (sourceType === "trace_project") {
      const setupPackageLabel = observeSetupPackageLabel({
        setupLanguage,
        setupProvider,
      });
      if (!setupPackageLabel) return TRACE_PROJECT_RERUN_COPY;
      return {
        ...TRACE_PROJECT_RERUN_COPY,
        description: `Run the ${setupPackageLabel} quality check again after the trace-source fix, then compare the result.`,
        title: `Rerun ${setupPackageLabel} quality check`,
      };
    }
    return EVAL_RERUN_COPY;
  }
  if (sourceType === "trace_project") {
    const setupPackageLabel = observeSetupPackageLabel({
      setupLanguage,
      setupProvider,
    });
    const fallbackCopy =
      TRACE_PROJECT_STEP_COPY[step] ||
      TRACE_PROJECT_STEP_COPY[EVAL_CREATE_ONBOARDING_STEPS.SCORER];
    if (!setupPackageLabel) return fallbackCopy;

    if (step === EVAL_CREATE_ONBOARDING_STEPS.DATA) {
      return {
        ...fallbackCopy,
        description: `${setupPackageLabel} trace source is selected. Next, create a quality check from that trace source.`,
        title: `Use ${setupPackageLabel} trace source`,
      };
    }

    if (step === EVAL_CREATE_ONBOARDING_STEPS.RUN) {
      return {
        ...fallbackCopy,
        description: `Run the saved quality check on ${setupPackageLabel} traces so the first result is reviewable.`,
        title: `Run ${setupPackageLabel} quality check`,
      };
    }

    return {
      ...fallbackCopy,
      description: `A starter quality check is loaded for ${setupPackageLabel} traces. Create it, then run it once.`,
      title: `Create ${setupPackageLabel} quality check`,
    };
  }
  return STEP_COPY[step] || STEP_COPY[EVAL_CREATE_ONBOARDING_STEPS.SCORER];
};

export const getEvalCreateInitialSourceTab = ({
  isOnboarding,
  sourceType,
  step,
} = {}) => {
  if (!isOnboarding) return EVAL_CREATE_SOURCE_TABS.CUSTOM;

  if (step === EVAL_CREATE_ONBOARDING_STEPS.DATA) {
    return SOURCE_TYPE_TO_TAB[sourceType] || EVAL_CREATE_SOURCE_TABS.DATASET;
  }

  if (
    [
      EVAL_CREATE_ONBOARDING_STEPS.SCORER,
      EVAL_CREATE_ONBOARDING_STEPS.RUN,
    ].includes(step) &&
    sourceType
  ) {
    return SOURCE_TYPE_TO_TAB[sourceType] || EVAL_CREATE_SOURCE_TABS.CUSTOM;
  }

  return EVAL_CREATE_SOURCE_TABS.CUSTOM;
};

export const getEvalOnboardingSourceSummary = ({
  isOnboarding,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  step,
} = {}) => {
  if (!isOnboarding || !sourceId) {
    return null;
  }

  const setupPackageLabel =
    sourceType === "trace_project"
      ? observeSetupPackageLabel({ setupLanguage, setupProvider })
      : "";
  const sourceLabel = SOURCE_TYPE_LABELS[sourceType] || "Source";
  const summaryLabel = setupPackageLabel
    ? `${setupPackageLabel} trace project`
    : sourceLabel;

  if (step === EVAL_CREATE_ONBOARDING_STEPS.DATA) {
    return {
      description: setupPackageLabel
        ? `Source is locked to this ${setupPackageLabel} trace project. Add a scorer next.`
        : sourceType === "trace_project"
          ? "Source is locked to this trace project. Add a scorer next."
          : "Use this source to add a scorer next.",
      label:
        sourceType === "trace_project"
          ? `${summaryLabel} locked`
          : `${summaryLabel} selected`,
    };
  }

  if (step === EVAL_CREATE_ONBOARDING_STEPS.SCORER) {
    return {
      description: setupPackageLabel
        ? `Starter scorer is loaded for ${setupPackageLabel} traces. Create the quality check, then run it once.`
        : sourceType === "trace_project"
          ? "Starter scorer is loaded for this trace project. Create the quality check, then run it once."
          : "Starter scorer is ready. Edit it or save to run this source.",
      label:
        sourceType === "trace_project"
          ? `${summaryLabel} locked`
          : `${summaryLabel} ready`,
    };
  }

  return {
    description: setupPackageLabel
      ? `Run the saved quality check on ${setupPackageLabel} traces.`
      : sourceType === "trace_project"
        ? "Run the saved quality check on this trace project."
        : "Run the saved scorer on this source.",
    label: `${summaryLabel} ready`,
  };
};

export const getEvalStarterScorer = ({ sourceId, sourceType } = {}) => {
  const sourceSlug = safeKeyPart(sourceId || sourceType, "source").slice(0, 12);
  const sourceLabel = SOURCE_TYPE_LABELS[sourceType] || "source";

  return {
    code: EVAL_STARTER_SCORER_CODE,
    codeLanguage: "python",
    description: `Starter scorer for ${sourceLabel.toLowerCase()}.`,
    evalType: "code",
    name: `output-quality-${sourceSlug}`.toLowerCase(),
    outputType: "percentage",
    passThreshold: 0.7,
  };
};

export const buildEvalCreateDraftHref = (draftId, search = "") => {
  const query = toSearchParams(search).toString();
  return `/dashboard/evaluations/create/${draftId}${query ? `?${query}` : ""}`;
};

export const buildEvalSourceSetupHref = ({
  quickStartAttribution,
  search,
} = {}) =>
  appendEvalOnboardingAttributionToHref(
    "/dashboard/develop?source=onboarding&action=create-eval-dataset",
    evalQuickStartAttributionInput({ quickStartAttribution, search }),
  );

export const buildEvalScorerSourceHref = ({
  evalId,
  quickStartAttribution,
  search,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType = "dataset",
  traceId,
} = {}) => {
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("step", EVAL_CREATE_ONBOARDING_STEPS.SCORER);
  params.set("source_type", sourceType || "dataset");
  if (sourceId) params.set("source_id", sourceId);
  appendTraceContextParams(params, { traceId });
  appendSetupIntentParams(params, {
    search,
    setupIntent,
    setupLanguage,
    setupProvider,
  });

  return appendEvalOnboardingAttributionToHref(
    `/dashboard/evaluations/create${evalId ? `/${evalId}` : ""}?${params.toString()}`,
    evalQuickStartAttributionInput({ quickStartAttribution, search }),
  );
};

export const buildEvalScorerEditHref = ({
  evalId,
  previousRunId,
  quickStartAttribution,
  rerunFrom,
  search,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  if (!evalId) return null;

  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("step", EVAL_CREATE_ONBOARDING_STEPS.SCORER);
  if (sourceType) params.set("source_type", sourceType);
  if (sourceId) params.set("source_id", sourceId);
  appendEvalFixRerunParams(params, { previousRunId, rerunFrom });
  appendTraceContextParams(params, { traceId });
  appendSetupIntentParams(params, {
    search,
    setupIntent,
    setupLanguage,
    setupProvider,
  });

  return appendEvalOnboardingAttributionToHref(
    `/dashboard/evaluations/create/${evalId}?${params.toString()}`,
    evalQuickStartAttributionInput({ quickStartAttribution, search }),
  );
};

export const buildEvalRunStepHref = ({
  evalId,
  previousRunId,
  quickStartAttribution,
  rerunFrom,
  search,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("step", EVAL_CREATE_ONBOARDING_STEPS.RUN);
  if (sourceType) params.set("source_type", sourceType);
  if (sourceId) params.set("source_id", sourceId);
  appendEvalFixRerunParams(params, { previousRunId, rerunFrom });
  appendTraceContextParams(params, { traceId });
  appendSetupIntentParams(params, {
    search,
    setupIntent,
    setupLanguage,
    setupProvider,
  });

  return appendEvalOnboardingAttributionToHref(
    `/dashboard/evaluations/create/${evalId}?${params.toString()}`,
    evalQuickStartAttributionInput({ quickStartAttribution, search }),
  );
};

export const getEvalRunResultId = (result = {}) =>
  result?.log_id ||
  result?.eval_log_id ||
  result?.run_id ||
  result?.eval_run_id ||
  result?.eval_task_id ||
  result?.evaluation_id ||
  null;

const numericFailureCount = (value) => {
  if (value === null || value === undefined || value === "") return null;
  if (Array.isArray(value)) return value.length;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.trunc(parsed) : null;
};

export const getEvalRunFailureCount = (result = {}) => {
  const candidates = [
    result?.failure_count,
    result?.failureCount,
    result?.failed_count,
    result?.failedCount,
    result?.failed_spans_count,
    result?.failedSpansCount,
    result?.failed_spans,
    result?.failedSpans,
    result?.failures,
    result?.failed,
  ];
  for (const candidate of candidates) {
    const count = numericFailureCount(candidate);
    if (count !== null) return count;
  }

  const status = String(result?.status || result?.result || "").toLowerCase();
  if (["fail", "failed", "failure", "error", "errored"].includes(status)) {
    return 1;
  }
  return null;
};

export const getEvalUsageLogId = (log = {}) => {
  const detail = log?.detail || {};
  return (
    log?.id ||
    log?.run_id ||
    log?.log_id ||
    log?.eval_log_id ||
    log?.eval_task_id ||
    log?.evaluation_id ||
    detail?.id ||
    detail?.run_id ||
    detail?.log_id ||
    detail?.eval_log_id ||
    detail?.eval_task_id ||
    detail?.evaluation_id ||
    null
  );
};

export const evalUsageLogMatchesRun = (log = {}, runId) => {
  if (!runId) return false;

  const detail = log?.detail || {};
  const candidates = [
    log?.id,
    log?.run_id,
    log?.log_id,
    log?.eval_log_id,
    log?.eval_task_id,
    log?.evaluation_id,
    detail?.id,
    detail?.run_id,
    detail?.log_id,
    detail?.eval_log_id,
    detail?.eval_task_id,
    detail?.evaluation_id,
  ];
  const target = String(runId);

  return candidates.some((candidate) => String(candidate || "") === target);
};

export const getEvalUsageReviewOutcome = (log = {}) => {
  const result = String(log?.result || "").toLowerCase();
  const status = String(log?.status || "").toLowerCase();
  const score =
    typeof log?.score === "number" ? log.score : Number.parseFloat(log?.score);
  const failedValues = new Set([
    "failed",
    "fail",
    "failure",
    "error",
    "errored",
  ]);
  const pendingValues = new Set([
    "pending",
    "queued",
    "running",
    "in_progress",
    "processing",
    "started",
  ]);
  const passedValues = new Set([
    "passed",
    "pass",
    "success",
    "succeeded",
    "completed",
    "complete",
  ]);

  if (
    failedValues.has(result) ||
    failedValues.has(status) ||
    ["cancelled", "canceled"].includes(status)
  ) {
    return "failure_reviewed";
  }
  if (Number.isFinite(score) && score < 0.7) {
    return "weak_result_reviewed";
  }
  if (
    pendingValues.has(result) ||
    pendingValues.has(status) ||
    (!Number.isFinite(score) &&
      !passedValues.has(result) &&
      !passedValues.has(status))
  ) {
    return "pending_result";
  }
  return "result_summary_reviewed";
};

export const getEvalReviewActionKind = ({
  canComplete = false,
  log,
  scorerEditHref,
  sourceFixHref,
} = {}) => {
  const reviewOutcome = getEvalUsageReviewOutcome(log);
  const shouldFixSource = ["failure_reviewed", "weak_result_reviewed"].includes(
    reviewOutcome,
  );

  if (shouldFixSource && sourceFixHref) {
    return EVAL_REVIEW_ACTIONS.SOURCE_FIX;
  }
  if (reviewOutcome === "result_summary_reviewed" && canComplete) {
    return EVAL_REVIEW_ACTIONS.COMPLETE;
  }
  if (reviewOutcome === "pending_result") {
    return null;
  }
  if (scorerEditHref) {
    return EVAL_REVIEW_ACTIONS.SCORER_EDIT;
  }
  if (sourceFixHref) {
    return EVAL_REVIEW_ACTIONS.SOURCE_FIX;
  }
  return null;
};

export const getEvalReviewOnboardingParams = (search = "") => {
  const params = toSearchParams(search);
  const journeyStep = EVAL_JOURNEY_REVIEW_STEPS[params.get("journey_step")];
  const step = params.get("step") || journeyStep || null;
  const tab = params.get("tab") || "usage";

  return {
    isOnboarding:
      (params.get("source") === "onboarding" || Boolean(journeyStep)) &&
      step === EVAL_REVIEW_STEP,
    previousRunId: params.get("previous_run_id"),
    rerunFrom: normalizeFixRerunOrigin(params.get("rerun_from")),
    runId: params.get("run_id"),
    ...setupIntentFromSearch(params),
    sourceId: params.get("source_id"),
    sourceType: params.get("source_type"),
    step,
    tab,
    traceId: params.get("trace_id"),
    tourAnchor: params.get("tour_anchor"),
  };
};

export const getEvalFailureActionOnboardingParams = (search = "") => {
  const params = toSearchParams(search);
  const journeyStep = EVAL_JOURNEY_REVIEW_STEPS[params.get("journey_step")];
  const step = params.get("step") || journeyStep || null;

  return {
    isOnboarding:
      (params.get("source") === "onboarding" || Boolean(journeyStep)) &&
      [EVAL_REVIEW_STEP, EVAL_FIX_STEP].includes(step),
    previousRunId: params.get("previous_run_id"),
    rerunFrom: normalizeFixRerunOrigin(params.get("rerun_from")),
    runId: params.get("run_id"),
    ...setupIntentFromSearch(params),
    sourceId: params.get("source_id"),
    sourceType: params.get("source_type"),
    step,
    traceId: params.get("trace_id"),
    tourAnchor: params.get("tour_anchor"),
  };
};

export const getEvalReviewOnboardingCopy = ({
  rerunFrom,
  setupLanguage,
  setupProvider,
  sourceType,
} = {}) => {
  if (rerunFrom) return EVAL_REPAIR_REVIEW_COPY;
  if (sourceType === "trace_project") {
    return traceProjectReviewCopy({ setupLanguage, setupProvider });
  }
  return EVAL_REVIEW_COPY;
};

export const getEvalSourceFixOnboardingParams = (search = "") => {
  const params = toSearchParams(search);
  const journeyStep = EVAL_JOURNEY_REVIEW_STEPS[params.get("journey_step")];
  const step = params.get("step") || journeyStep || null;

  return {
    evalId: params.get("eval_id"),
    isOnboarding:
      (params.get("source") === "onboarding" || Boolean(journeyStep)) &&
      step === EVAL_FIX_STEP,
    runId: params.get("run_id"),
    ...setupIntentFromSearch(params),
    sourceId: params.get("source_id"),
    sourceType: params.get("source_type"),
    step,
    traceId: params.get("trace_id"),
    tourAnchor: params.get("tour_anchor"),
  };
};

export const getEvalSourceFixOnboardingCopy = ({ sourceType } = {}) =>
  EVAL_SOURCE_FIX_COPY[sourceType] || {
    description:
      "Update the source that produced this result, then rerun the quality check.",
    title: "Fix the source",
  };

export const buildEvalReviewStepHref = ({
  evalId,
  previousRunId,
  quickStartAttribution,
  rerunFrom,
  runId,
  search,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  const basePath = evalId
    ? `/dashboard/evaluations/${evalId}`
    : "/dashboard/evaluations/usage";
  const params = new URLSearchParams();
  params.set("tab", "usage");
  params.set("source", "onboarding");
  params.set("step", EVAL_REVIEW_STEP);
  if (runId) params.set("run_id", runId);
  if (sourceType) params.set("source_type", sourceType);
  if (sourceId) params.set("source_id", sourceId);
  appendEvalFixRerunParams(params, { previousRunId, rerunFrom });
  appendTraceContextParams(params, { traceId });
  appendSetupIntentParams(params, {
    search,
    setupIntent,
    setupLanguage,
    setupProvider,
  });

  return appendEvalOnboardingAttributionToHref(
    `${basePath}?${params.toString()}`,
    evalQuickStartAttributionInput({ quickStartAttribution, search }),
  );
};

export const buildEvalReviewDetailHref = (evalId, search = "") => {
  const reviewParams = getEvalReviewOnboardingParams(search);
  const basePath = `/dashboard/evaluations/${evalId}`;

  if (!reviewParams.isOnboarding) return basePath;

  return buildEvalReviewStepHref({
    evalId,
    runId: reviewParams.runId,
    previousRunId: reviewParams.previousRunId,
    rerunFrom: reviewParams.rerunFrom,
    search,
    setupLanguage: reviewParams.setupLanguage,
    setupProvider: reviewParams.setupProvider,
    sourceId: reviewParams.sourceId,
    sourceType: reviewParams.sourceType,
    traceId: reviewParams.traceId,
  });
};

export const buildEvalSourceFixHref = ({
  evalId,
  quickStartAttribution,
  runId,
  search,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  if (!sourceId || !sourceType) return null;

  let basePath = null;
  if (sourceType === "dataset") {
    basePath = `/dashboard/develop/${sourceId}`;
  } else if (sourceType === "simulation") {
    basePath = `/dashboard/simulate/test/${sourceId}/runs`;
  } else if (["trace", "trace_project"].includes(sourceType)) {
    basePath = `/dashboard/observe/${sourceId}/llm-tracing`;
  }
  if (!basePath) return null;

  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("step", EVAL_FIX_STEP);
  params.set("source_type", sourceType);
  params.set("source_id", sourceId);
  if (evalId) params.set("eval_id", evalId);
  if (runId) params.set("run_id", runId);
  appendTraceContextParams(params, { traceId });
  appendSetupIntentParams(params, {
    search,
    setupIntent,
    setupLanguage,
    setupProvider,
  });

  return appendEvalOnboardingAttributionToHref(
    `${basePath}?${params.toString()}`,
    evalQuickStartAttributionInput({ quickStartAttribution, search }),
  );
};

export const buildEvalPostRepairHomeHref = ({
  previousRunId,
  quickStartAttribution,
  rerunFrom,
  runId,
  search,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("target_event", FIRST_QUALITY_LOOP_EVENT);
  if (runId) params.set("run_id", runId);
  if (sourceType) params.set("source_type", sourceType);
  if (sourceId) params.set("source_id", sourceId);
  appendEvalFixRerunParams(params, { previousRunId, rerunFrom });
  appendTraceContextParams(params, { traceId });
  appendSetupIntentParams(params, {
    search,
    setupIntent,
    setupLanguage,
    setupProvider,
  });

  return appendEvalOnboardingAttributionToHref(
    `/dashboard/home?${params.toString()}`,
    evalQuickStartAttributionInput({ quickStartAttribution, search }),
  );
};

export const evalCreateOnboardingStage = (step) =>
  STEP_TO_STAGE[step] || STEP_TO_STAGE[EVAL_CREATE_ONBOARDING_STEPS.SCORER];

export const buildEvalRouteFocusPayload = ({
  draftId,
  previousRunId,
  quickStartAttribution,
  rerunFrom,
  runId,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  step,
  traceId,
} = {}) => {
  const normalizedStep = validSteps.has(step)
    ? step
    : EVAL_CREATE_ONBOARDING_STEPS.SCORER;
  const artifactId = safeKeyPart(
    sourceId || draftId || normalizedStep,
    DEFAULT_ARTIFACT_ID,
  );

  return {
    eventName: "onboarding_eval_route_focus_viewed",
    primaryPath: "evals",
    stage: evalCreateOnboardingStage(normalizedStep),
    source: "eval_create_onboarding",
    artifactType: "eval",
    artifactId,
    metadata: compactMetadata({
      draft_id: draftId,
      previous_run_id: previousRunId,
      rerun_from: normalizeFixRerunOrigin(rerunFrom),
      run_id: runId,
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      step: normalizedStep,
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "onboarding_eval_route_focus_viewed",
      safeKeyPart(normalizedStep, "step"),
      artifactId,
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalSourceSelectedPayload = ({
  draftId,
  quickStartAttribution,
  rowType,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  step,
  surface,
  traceId,
} = {}) => {
  const normalizedStep = validSteps.has(step)
    ? step
    : EVAL_CREATE_ONBOARDING_STEPS.DATA;
  const artifactId = safeKeyPart(sourceId, "eval-source");

  return {
    eventName: "onboarding_eval_source_selected",
    primaryPath: "evals",
    stage: evalCreateOnboardingStage(normalizedStep),
    source: "eval_create_onboarding",
    artifactType: SOURCE_TYPE_ARTIFACT_TYPES[sourceType] || "eval",
    artifactId,
    metadata: compactMetadata({
      draft_id: draftId,
      row_type: rowType,
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      step: normalizedStep,
      surface,
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "onboarding_eval_source_selected",
      safeKeyPart(sourceType, "source"),
      artifactId,
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalDatasetCreatedPayload = ({
  datasetId,
  quickStartAttribution,
  sourceMethod,
} = {}) => {
  const artifactId = safeKeyPart(datasetId, "eval-source");

  return {
    eventName: "eval_dataset_created",
    primaryPath: "evals",
    stage: "create_eval_dataset",
    source: "eval_create_onboarding",
    artifactType: "dataset",
    artifactId,
    metadata: compactMetadata({
      dataset_id: datasetId,
      source_id: datasetId,
      source_method: sourceMethod,
      source_type: "dataset",
      step: EVAL_CREATE_ONBOARDING_STEPS.DATA,
    }),
    idempotencyKey: ["eval_dataset_created", "dataset", artifactId].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalScorerCreatedPayload = ({
  evalId,
  evalType,
  isComposite = false,
  quickStartAttribution,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  step,
  traceId,
} = {}) => {
  const artifactId = safeKeyPart(evalId || sourceId, "eval-scorer");

  return {
    eventName: "eval_scorer_created",
    primaryPath: "evals",
    stage: "add_eval_scorer",
    source: "eval_create_onboarding",
    artifactType: "eval_scorer",
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      eval_type: evalType,
      is_composite: Boolean(isComposite),
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      step,
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "eval_scorer_created",
      safeKeyPart(sourceId, "no-source"),
      artifactId,
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalRunClickedPayload = ({
  evalId,
  evalType,
  isComposite = false,
  mode,
  previousRunId,
  quickStartAttribution,
  rerunFrom,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  const artifactId = safeKeyPart(evalId || sourceId, "eval-run");
  const normalizedRerunFrom = normalizeFixRerunOrigin(rerunFrom);

  return {
    eventName: "onboarding_eval_run_clicked",
    primaryPath: "evals",
    stage: "run_eval",
    source: "eval_create_onboarding",
    artifactType: "eval",
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      eval_type: evalType,
      is_composite: Boolean(isComposite),
      mode,
      previous_run_id: previousRunId,
      rerun_from: normalizedRerunFrom,
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "onboarding_eval_run_clicked",
      safeKeyPart(sourceId, "no-source"),
      artifactId,
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalRunCompletedPayload = ({
  evalId,
  evalType,
  isComposite = false,
  mode,
  quickStartAttribution,
  result = {},
  runId,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  const resultRunId = getEvalRunResultId(result);
  const failureCount = getEvalRunFailureCount(result);
  const artifactId = safeKeyPart(runId || resultRunId || evalId, "eval-run");

  return {
    eventName: "eval_run_completed",
    primaryPath: "evals",
    stage: "run_eval",
    source: "eval_create_onboarding",
    artifactType: "eval_run",
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      eval_type: evalType,
      eval_log_id: result?.eval_log_id,
      eval_task_id: result?.eval_task_id,
      evaluation_id: result?.evaluation_id,
      failure_count: failureCount,
      is_composite: Boolean(isComposite),
      log_id: result?.log_id,
      mode,
      run_id: runId || resultRunId,
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      status: result?.status || "completed",
      step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "eval_run_completed",
      safeKeyPart(sourceId, "no-source"),
      safeKeyPart(evalId, "no-eval"),
      artifactId,
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalFixRerunCompletedPayload = ({
  evalId,
  evalType,
  isComposite = false,
  mode,
  previousRunId,
  quickStartAttribution,
  rerunFrom,
  result = {},
  runId,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  const normalizedRerunFrom = normalizeFixRerunOrigin(rerunFrom);
  const resultRunId = getEvalRunResultId(result);
  const failureCount = getEvalRunFailureCount(result);
  const artifactId = safeKeyPart(runId || resultRunId || evalId, "eval-run");

  return {
    eventName: "onboarding_eval_fix_rerun_completed",
    primaryPath: "evals",
    stage: "eval_next_loop",
    source: "eval_review_onboarding",
    artifactType: "eval_run",
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      eval_type: evalType,
      eval_log_id: result?.eval_log_id,
      eval_task_id: result?.eval_task_id,
      evaluation_id: result?.evaluation_id,
      failure_count: failureCount,
      is_composite: Boolean(isComposite),
      log_id: result?.log_id,
      mode,
      previous_run_id: previousRunId,
      rerun_from: normalizedRerunFrom,
      run_id: runId || resultRunId,
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      status: result?.status || "completed",
      step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "onboarding_eval_fix_rerun_completed",
      safeKeyPart(normalizedRerunFrom, "rerun"),
      safeKeyPart(previousRunId, "no-previous-run"),
      artifactId,
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalReviewRouteFocusPayload = ({
  evalId,
  previousRunId,
  quickStartAttribution,
  rerunFrom,
  route = "eval_detail",
  runId,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  const artifactId = safeKeyPart(runId || evalId, EVAL_REVIEW_ARTIFACT_ID);

  return {
    eventName: "onboarding_eval_route_focus_viewed",
    primaryPath: "evals",
    stage: EVAL_REVIEW_STAGE,
    source: "eval_review_onboarding",
    artifactType: runId ? "eval_run" : "eval",
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      previous_run_id: previousRunId,
      rerun_from: normalizeFixRerunOrigin(rerunFrom),
      route,
      run_id: runId,
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      step: EVAL_REVIEW_STEP,
      tab: "usage",
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "onboarding_eval_route_focus_viewed",
      EVAL_REVIEW_STEP,
      artifactId,
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalSourceFixRouteFocusPayload = ({
  evalId,
  quickStartAttribution,
  route,
  runId,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  const artifactId = safeKeyPart(
    sourceId || runId,
    EVAL_SOURCE_FIX_ARTIFACT_ID,
  );

  return {
    eventName: "onboarding_eval_source_fix_route_viewed",
    primaryPath: "evals",
    stage: "eval_next_loop",
    source: "eval_review_onboarding",
    artifactType: artifactTypeForSource(sourceType, "eval_run"),
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      route,
      run_id: runId,
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      step: EVAL_FIX_STEP,
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "onboarding_eval_source_fix_route_viewed",
      safeKeyPart(sourceType, "source"),
      artifactId,
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalSourceFixRerunClickedPayload = ({
  evalId,
  quickStartAttribution,
  rerunRoute,
  route,
  runId,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  const artifactId = safeKeyPart(
    sourceId || evalId || runId,
    EVAL_SOURCE_FIX_ARTIFACT_ID,
  );

  return {
    eventName: "onboarding_eval_source_fix_rerun_clicked",
    primaryPath: "evals",
    stage: "eval_next_loop",
    source: "eval_review_onboarding",
    artifactType: artifactTypeForSource(sourceType, "eval_run"),
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      rerun_route: rerunRoute,
      route,
      run_id: runId,
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      step: EVAL_FIX_STEP,
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "onboarding_eval_source_fix_rerun_clicked",
      safeKeyPart(sourceType, "source"),
      safeKeyPart(evalId || sourceId || runId, "eval-source-fix"),
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalFailuresReviewedPayload = ({
  evalId,
  evalLogId,
  quickStartAttribution,
  reviewOutcome,
  reviewSurface = "usage_log_detail",
  rowSource,
  runId,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  const artifactId = safeKeyPart(
    runId || evalLogId || evalId,
    EVAL_REVIEW_ARTIFACT_ID,
  );

  return {
    eventName: "eval_failures_reviewed",
    primaryPath: "evals",
    stage: EVAL_REVIEW_STAGE,
    source: "eval_review_onboarding",
    artifactType: "eval_run",
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      eval_log_id: evalLogId,
      review_outcome: reviewOutcome,
      review_surface: reviewSurface,
      row_source: rowSource,
      run_id: runId,
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      step: EVAL_REVIEW_STEP,
      tab: "usage",
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "eval_failures_reviewed",
      safeKeyPart(runId || evalLogId, "no-run"),
      safeKeyPart(evalId, "no-eval"),
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalFixRerunReviewedPayload = ({
  evalId,
  evalLogId,
  previousRunId,
  quickStartAttribution,
  rerunFrom,
  reviewOutcome,
  reviewSurface = "usage_log_detail",
  rowSource,
  runId,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  const normalizedRerunFrom = normalizeFixRerunOrigin(rerunFrom);
  const artifactId = safeKeyPart(
    runId || evalLogId || evalId,
    EVAL_REVIEW_ARTIFACT_ID,
  );

  return {
    eventName: "onboarding_eval_fix_rerun_reviewed",
    primaryPath: "evals",
    stage: EVAL_REVIEW_STAGE,
    source: "eval_review_onboarding",
    artifactType: "eval_run",
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      eval_log_id: evalLogId,
      previous_run_id: previousRunId,
      rerun_from: normalizedRerunFrom,
      review_outcome: reviewOutcome,
      review_surface: reviewSurface,
      row_source: rowSource,
      run_id: runId,
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      step: EVAL_REVIEW_STEP,
      tab: "usage",
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "onboarding_eval_fix_rerun_reviewed",
      safeKeyPart(normalizedRerunFrom, "rerun"),
      safeKeyPart(previousRunId, "no-previous-run"),
      artifactId,
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalFailureActionCreatedPayload = ({
  actionType,
  evalId,
  evalLogId,
  feedbackId,
  fixRoute,
  quickStartAttribution,
  rowSource,
  runId,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  step,
  traceId,
} = {}) => {
  const artifactId = safeKeyPart(
    feedbackId || evalLogId || runId || evalId,
    EVAL_FIX_ARTIFACT_ID,
  );

  return {
    eventName: "eval_failure_action_created",
    primaryPath: "evals",
    stage: "eval_next_loop",
    source: "eval_review_onboarding",
    artifactType: "eval_run",
    artifactId,
    metadata: compactMetadata({
      action_type: actionType,
      eval_id: evalId,
      eval_log_id: evalLogId,
      feedback_id: feedbackId,
      fix_route: fixRoute,
      row_source: rowSource,
      run_id: runId,
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      step: step || EVAL_FIX_STEP,
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "eval_failure_action_created",
      safeKeyPart(feedbackId || evalLogId, "no-feedback"),
      safeKeyPart(evalId, "no-eval"),
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalSourceFixCtaClickedPayload = ({
  evalId,
  evalLogId,
  fixRoute,
  quickStartAttribution,
  rowSource,
  runId,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  const artifactId = safeKeyPart(
    sourceId || evalLogId || runId || evalId,
    EVAL_FIX_ARTIFACT_ID,
  );

  return {
    eventName: "onboarding_eval_source_fix_cta_clicked",
    primaryPath: "evals",
    stage: "eval_next_loop",
    source: "eval_review_onboarding",
    artifactType: artifactTypeForSource(sourceType, "eval_run"),
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      eval_log_id: evalLogId,
      fix_route: fixRoute,
      row_source: rowSource,
      run_id: runId,
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      step: EVAL_FIX_STEP,
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "onboarding_eval_source_fix_cta_clicked",
      safeKeyPart(sourceId || evalLogId, "no-source"),
      safeKeyPart(evalId, "no-eval"),
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalScorerEditCtaClickedPayload = ({
  editRoute,
  evalId,
  evalLogId,
  quickStartAttribution,
  rowSource,
  runId,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  const artifactId = safeKeyPart(
    evalId || evalLogId || runId,
    "eval-scorer-edit",
  );

  return {
    eventName: "onboarding_eval_scorer_edit_cta_clicked",
    primaryPath: "evals",
    stage: "eval_next_loop",
    source: "eval_review_onboarding",
    artifactType: "eval_scorer",
    artifactId,
    metadata: compactMetadata({
      edit_route: editRoute,
      eval_id: evalId,
      eval_log_id: evalLogId,
      row_source: rowSource,
      run_id: runId,
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      step: EVAL_CREATE_ONBOARDING_STEPS.SCORER,
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "onboarding_eval_scorer_edit_cta_clicked",
      safeKeyPart(evalId, "no-eval"),
      safeKeyPart(evalLogId || runId, "no-run"),
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};

export const buildEvalFirstQualityLoopCompletedPayload = ({
  evalId,
  evalLogId,
  previousRunId,
  quickStartAttribution,
  rerunFrom,
  reviewOutcome,
  runId,
  setupIntent,
  setupLanguage,
  setupProvider,
  sourceId,
  sourceType,
  traceId,
} = {}) => {
  const normalizedRerunFrom = normalizeFixRerunOrigin(rerunFrom);
  const artifactId = safeKeyPart(runId || evalLogId || evalId, "eval-run");

  return {
    eventName: FIRST_QUALITY_LOOP_EVENT,
    primaryPath: "evals",
    stage: "activated",
    source: "eval_review_onboarding",
    artifactType: "eval_run",
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      eval_log_id: evalLogId,
      previous_run_id: previousRunId,
      rerun_from: normalizedRerunFrom,
      review_outcome: reviewOutcome,
      run_id: runId,
      ...setupIntentMetadata({ setupIntent, setupLanguage, setupProvider }),
      source_id: sourceId,
      source_type: sourceType,
      step: EVAL_REVIEW_STEP,
      tab: "usage",
      ...traceContextMetadata({ traceId }),
    }),
    idempotencyKey: [
      "eval_onboarding",
      FIRST_QUALITY_LOOP_EVENT,
      safeKeyPart(previousRunId, "no-previous-run"),
      artifactId,
    ].join(":"),
    isSample: false,
    ...setupQuickStartAttributionParams(quickStartAttribution),
  };
};
