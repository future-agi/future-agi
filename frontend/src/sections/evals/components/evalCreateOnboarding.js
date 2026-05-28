const DEFAULT_ARTIFACT_ID = "eval-onboarding";
const EVAL_REVIEW_ARTIFACT_ID = "eval-review";
const EVAL_FIX_ARTIFACT_ID = "eval-failure-action";
const EVAL_SOURCE_FIX_ARTIFACT_ID = "eval-source-fix-route";
const EVAL_FIX_STEP = "fix-eval-failure";
const EVAL_REVIEW_STEP = "review";
const EVAL_REVIEW_STAGE = "review_eval_failures";

export const EVAL_FIX_RERUN_ORIGINS = {
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

const STEP_COPY = {
  [EVAL_CREATE_ONBOARDING_STEPS.DATA]: {
    currentStep: "Source",
    description: "Choose the data or trace source before adding the scorer.",
    title: "Create the eval source",
    steps: [
      { label: "Source", complete: false },
      { label: "Scorer", complete: false },
      { label: "Run", complete: false },
    ],
  },
  [EVAL_CREATE_ONBOARDING_STEPS.SCORER]: {
    currentStep: "Scorer",
    description: "Save one scorer so FutureAGI can evaluate this source.",
    title: "Add the eval scorer",
    steps: [
      { label: "Source", complete: true },
      { label: "Scorer", complete: false },
      { label: "Run", complete: false },
    ],
  },
  [EVAL_CREATE_ONBOARDING_STEPS.RUN]: {
    currentStep: "Run",
    description: "Run the scorer once so the first eval result is reviewable.",
    title: "Run the first eval",
    steps: [
      { label: "Source", complete: true },
      { label: "Scorer", complete: true },
      { label: "Run", complete: false },
    ],
  },
};

const EVAL_REVIEW_COPY = {
  currentStep: "Review",
  description: "Inspect failures or summary before deciding what to fix next.",
  title: "Review the eval result",
  steps: [
    { label: "Source", complete: true },
    { label: "Scorer", complete: true },
    { label: "Run", complete: true },
    { label: "Review", complete: false },
  ],
};

const EVAL_REPAIR_REVIEW_COPY = {
  currentStep: "Review rerun",
  description:
    "This run follows a repair action. Check the result before deciding whether to fix more or continue.",
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

const EVAL_SOURCE_FIX_COPY = {
  dataset: {
    description:
      "Update this dataset, then rerun the eval to confirm the failure is fixed.",
    title: "Fix eval source",
  },
  trace: {
    description:
      "Review the trace evidence and adjust the source workflow, then rerun the eval.",
    title: "Fix eval source",
  },
  trace_project: {
    description:
      "Review the traces or project setup that produced this eval result, then rerun the eval.",
    title: "Fix eval source",
  },
};

const validSteps = new Set(Object.values(EVAL_CREATE_ONBOARDING_STEPS));
const validFixRerunOrigins = new Set(Object.values(EVAL_FIX_RERUN_ORIGINS));

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

const toSearchParams = (search = "") =>
  search instanceof URLSearchParams
    ? new URLSearchParams(search)
    : new URLSearchParams(search);

const normalizeFixRerunOrigin = (value) =>
  validFixRerunOrigins.has(value) ? value : null;

const appendEvalFixRerunParams = (
  params,
  { previousRunId, rerunFrom } = {},
) => {
  const normalizedRerunFrom = normalizeFixRerunOrigin(rerunFrom);
  if (normalizedRerunFrom) params.set("rerun_from", normalizedRerunFrom);
  if (previousRunId) params.set("previous_run_id", previousRunId);
};

export const getEvalCreateOnboardingParams = (search = "") => {
  const params = toSearchParams(search);
  const rawStep = params.get("step");
  const step = validSteps.has(rawStep)
    ? rawStep
    : EVAL_CREATE_ONBOARDING_STEPS.SCORER;

  return {
    isOnboarding: params.get("source") === "onboarding",
    previousRunId: params.get("previous_run_id"),
    rerunFrom: normalizeFixRerunOrigin(params.get("rerun_from")),
    runId: params.get("run_id"),
    sourceId: params.get("source_id"),
    sourceType: params.get("source_type"),
    step,
  };
};

export const getEvalCreateOnboardingCopy = ({ step } = {}) =>
  STEP_COPY[step] || STEP_COPY[EVAL_CREATE_ONBOARDING_STEPS.SCORER];

export const getEvalCreateInitialSourceTab = ({
  isOnboarding,
  sourceType,
  step,
} = {}) => {
  if (!isOnboarding) return EVAL_CREATE_SOURCE_TABS.CUSTOM;

  if (step === EVAL_CREATE_ONBOARDING_STEPS.DATA) {
    return SOURCE_TYPE_TO_TAB[sourceType] || EVAL_CREATE_SOURCE_TABS.DATASET;
  }

  if (step === EVAL_CREATE_ONBOARDING_STEPS.RUN && sourceType) {
    return SOURCE_TYPE_TO_TAB[sourceType] || EVAL_CREATE_SOURCE_TABS.CUSTOM;
  }

  return EVAL_CREATE_SOURCE_TABS.CUSTOM;
};

export const getEvalOnboardingSourceSummary = ({
  isOnboarding,
  sourceId,
  sourceType,
  step,
} = {}) => {
  if (
    !isOnboarding ||
    !sourceId ||
    step === EVAL_CREATE_ONBOARDING_STEPS.DATA
  ) {
    return null;
  }

  return {
    description: "The next scorer you save will evaluate this source.",
    label: `${SOURCE_TYPE_LABELS[sourceType] || "Source"} ready`,
  };
};

export const buildEvalCreateDraftHref = (draftId, search = "") => {
  const query = toSearchParams(search).toString();
  return `/dashboard/evaluations/create/${draftId}${query ? `?${query}` : ""}`;
};

export const buildEvalSourceSetupHref = () =>
  "/dashboard/develop?source=onboarding&action=create-eval-dataset";

export const buildEvalScorerSourceHref = ({ sourceId } = {}) => {
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("step", EVAL_CREATE_ONBOARDING_STEPS.SCORER);
  params.set("source_type", "dataset");
  if (sourceId) params.set("source_id", sourceId);

  return `/dashboard/evaluations/create?${params.toString()}`;
};

export const buildEvalScorerEditHref = ({
  evalId,
  previousRunId,
  rerunFrom,
  sourceId,
  sourceType,
} = {}) => {
  if (!evalId) return null;

  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("step", EVAL_CREATE_ONBOARDING_STEPS.SCORER);
  if (sourceType) params.set("source_type", sourceType);
  if (sourceId) params.set("source_id", sourceId);
  appendEvalFixRerunParams(params, { previousRunId, rerunFrom });

  return `/dashboard/evaluations/create/${evalId}?${params.toString()}`;
};

export const buildEvalRunStepHref = ({
  evalId,
  previousRunId,
  rerunFrom,
  sourceId,
  sourceType,
} = {}) => {
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("step", EVAL_CREATE_ONBOARDING_STEPS.RUN);
  if (sourceType) params.set("source_type", sourceType);
  if (sourceId) params.set("source_id", sourceId);
  appendEvalFixRerunParams(params, { previousRunId, rerunFrom });

  return `/dashboard/evaluations/create/${evalId}?${params.toString()}`;
};

export const getEvalRunResultId = (result = {}) =>
  result?.run_id ||
  result?.eval_run_id ||
  result?.eval_task_id ||
  result?.evaluation_id ||
  result?.log_id ||
  null;

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

  if (["failed", "fail"].includes(result) || status === "error") {
    return "failure_reviewed";
  }
  if (Number.isFinite(score) && score < 0.7) {
    return "weak_result_reviewed";
  }
  return "result_summary_reviewed";
};

export const getEvalReviewOnboardingParams = (search = "") => {
  const params = toSearchParams(search);
  const step = params.get("step");
  const tab = params.get("tab") || "usage";

  return {
    isOnboarding:
      params.get("source") === "onboarding" && step === EVAL_REVIEW_STEP,
    previousRunId: params.get("previous_run_id"),
    rerunFrom: normalizeFixRerunOrigin(params.get("rerun_from")),
    runId: params.get("run_id"),
    sourceId: params.get("source_id"),
    sourceType: params.get("source_type"),
    step,
    tab,
  };
};

export const getEvalFailureActionOnboardingParams = (search = "") => {
  const params = toSearchParams(search);
  const step = params.get("step");

  return {
    isOnboarding:
      params.get("source") === "onboarding" &&
      [EVAL_REVIEW_STEP, EVAL_FIX_STEP].includes(step),
    previousRunId: params.get("previous_run_id"),
    rerunFrom: normalizeFixRerunOrigin(params.get("rerun_from")),
    runId: params.get("run_id"),
    sourceId: params.get("source_id"),
    sourceType: params.get("source_type"),
    step,
  };
};

export const getEvalReviewOnboardingCopy = ({ rerunFrom } = {}) =>
  rerunFrom ? EVAL_REPAIR_REVIEW_COPY : EVAL_REVIEW_COPY;

export const getEvalSourceFixOnboardingParams = (search = "") => {
  const params = toSearchParams(search);
  const step = params.get("step");

  return {
    evalId: params.get("eval_id"),
    isOnboarding:
      params.get("source") === "onboarding" && step === EVAL_FIX_STEP,
    runId: params.get("run_id"),
    sourceId: params.get("source_id"),
    sourceType: params.get("source_type"),
    step,
  };
};

export const getEvalSourceFixOnboardingCopy = ({ sourceType } = {}) =>
  EVAL_SOURCE_FIX_COPY[sourceType] || {
    description:
      "Update the source that produced this eval result, then rerun the eval.",
    title: "Fix eval source",
  };

export const buildEvalReviewStepHref = ({
  evalId,
  previousRunId,
  rerunFrom,
  runId,
  sourceId,
  sourceType,
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

  return `${basePath}?${params.toString()}`;
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
    sourceId: reviewParams.sourceId,
    sourceType: reviewParams.sourceType,
  });
};

export const buildEvalSourceFixHref = ({
  evalId,
  runId,
  sourceId,
  sourceType,
} = {}) => {
  if (!sourceId || !sourceType) return null;

  let basePath = null;
  if (sourceType === "dataset") {
    basePath = `/dashboard/develop/${sourceId}`;
  } else if (["trace", "trace_project"].includes(sourceType)) {
    basePath = `/dashboard/observe/${sourceId}`;
  }
  if (!basePath) return null;

  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("step", EVAL_FIX_STEP);
  params.set("source_type", sourceType);
  params.set("source_id", sourceId);
  if (evalId) params.set("eval_id", evalId);
  if (runId) params.set("run_id", runId);

  return `${basePath}?${params.toString()}`;
};

export const evalCreateOnboardingStage = (step) =>
  STEP_TO_STAGE[step] || STEP_TO_STAGE[EVAL_CREATE_ONBOARDING_STEPS.SCORER];

export const buildEvalRouteFocusPayload = ({
  draftId,
  runId,
  sourceId,
  sourceType,
  step,
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
    artifactType: "eval_route",
    artifactId,
    metadata: compactMetadata({
      draft_id: draftId,
      run_id: runId,
      source_id: sourceId,
      source_type: sourceType,
      step: normalizedStep,
    }),
    idempotencyKey: [
      "onboarding_eval_route_focus_viewed",
      safeKeyPart(normalizedStep, "step"),
      artifactId,
    ].join(":"),
    isSample: false,
  };
};

export const buildEvalSourceSelectedPayload = ({
  draftId,
  rowType,
  sourceId,
  sourceType,
  step,
  surface,
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
    artifactType: "eval_source",
    artifactId,
    metadata: compactMetadata({
      draft_id: draftId,
      row_type: rowType,
      source_id: sourceId,
      source_type: sourceType,
      step: normalizedStep,
      surface,
    }),
    idempotencyKey: [
      "onboarding_eval_source_selected",
      safeKeyPart(sourceType, "source"),
      artifactId,
    ].join(":"),
    isSample: false,
  };
};

export const buildEvalDatasetCreatedPayload = ({
  datasetId,
  sourceMethod,
} = {}) => {
  const artifactId = safeKeyPart(datasetId, "eval-source");

  return {
    eventName: "eval_dataset_created",
    primaryPath: "evals",
    stage: "create_eval_dataset",
    source: "eval_create_onboarding",
    artifactType: "eval_source",
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
  };
};

export const buildEvalScorerCreatedPayload = ({
  evalId,
  evalType,
  isComposite = false,
  sourceId,
  sourceType,
  step,
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
      source_id: sourceId,
      source_type: sourceType,
      step,
    }),
    idempotencyKey: [
      "eval_scorer_created",
      safeKeyPart(sourceId, "no-source"),
      artifactId,
    ].join(":"),
    isSample: false,
  };
};

export const buildEvalRunCompletedPayload = ({
  evalId,
  evalType,
  isComposite = false,
  mode,
  result = {},
  runId,
  sourceId,
  sourceType,
} = {}) => {
  const resultRunId = getEvalRunResultId(result);
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
      is_composite: Boolean(isComposite),
      log_id: result?.log_id,
      mode,
      run_id: runId || resultRunId,
      source_id: sourceId,
      source_type: sourceType,
      status: result?.status || "completed",
      step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
    }),
    idempotencyKey: [
      "eval_run_completed",
      safeKeyPart(sourceId, "no-source"),
      safeKeyPart(evalId, "no-eval"),
      artifactId,
    ].join(":"),
    isSample: false,
  };
};

export const buildEvalFixRerunCompletedPayload = ({
  evalId,
  evalType,
  isComposite = false,
  mode,
  previousRunId,
  rerunFrom,
  result = {},
  runId,
  sourceId,
  sourceType,
} = {}) => {
  const normalizedRerunFrom = normalizeFixRerunOrigin(rerunFrom);
  const resultRunId = getEvalRunResultId(result);
  const artifactId = safeKeyPart(runId || resultRunId || evalId, "eval-run");

  return {
    eventName: "onboarding_eval_fix_rerun_completed",
    primaryPath: "evals",
    stage: "fix_eval_source",
    source: "eval_review_onboarding",
    artifactType: "eval_run",
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      eval_type: evalType,
      eval_log_id: result?.eval_log_id,
      eval_task_id: result?.eval_task_id,
      evaluation_id: result?.evaluation_id,
      is_composite: Boolean(isComposite),
      log_id: result?.log_id,
      mode,
      previous_run_id: previousRunId,
      rerun_from: normalizedRerunFrom,
      run_id: runId || resultRunId,
      source_id: sourceId,
      source_type: sourceType,
      status: result?.status || "completed",
      step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
    }),
    idempotencyKey: [
      "onboarding_eval_fix_rerun_completed",
      safeKeyPart(normalizedRerunFrom, "rerun"),
      safeKeyPart(previousRunId, "no-previous-run"),
      artifactId,
    ].join(":"),
    isSample: false,
  };
};

export const buildEvalReviewRouteFocusPayload = ({
  evalId,
  previousRunId,
  rerunFrom,
  route = "eval_detail",
  runId,
} = {}) => {
  const artifactId = safeKeyPart(runId || evalId, EVAL_REVIEW_ARTIFACT_ID);

  return {
    eventName: "onboarding_eval_route_focus_viewed",
    primaryPath: "evals",
    stage: EVAL_REVIEW_STAGE,
    source: "eval_review_onboarding",
    artifactType: "eval_review_route",
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      previous_run_id: previousRunId,
      rerun_from: normalizeFixRerunOrigin(rerunFrom),
      route,
      run_id: runId,
      step: EVAL_REVIEW_STEP,
      tab: "usage",
    }),
    idempotencyKey: [
      "onboarding_eval_route_focus_viewed",
      EVAL_REVIEW_STEP,
      artifactId,
    ].join(":"),
    isSample: false,
  };
};

export const buildEvalSourceFixRouteFocusPayload = ({
  evalId,
  route,
  runId,
  sourceId,
  sourceType,
} = {}) => {
  const artifactId = safeKeyPart(
    sourceId || runId,
    EVAL_SOURCE_FIX_ARTIFACT_ID,
  );

  return {
    eventName: "onboarding_eval_source_fix_route_viewed",
    primaryPath: "evals",
    stage: "fix_eval_source",
    source: "eval_review_onboarding",
    artifactType: "eval_source_fix_route",
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      route,
      run_id: runId,
      source_id: sourceId,
      source_type: sourceType,
      step: EVAL_FIX_STEP,
    }),
    idempotencyKey: [
      "onboarding_eval_source_fix_route_viewed",
      safeKeyPart(sourceType, "source"),
      artifactId,
    ].join(":"),
    isSample: false,
  };
};

export const buildEvalSourceFixRerunClickedPayload = ({
  evalId,
  rerunRoute,
  route,
  runId,
  sourceId,
  sourceType,
} = {}) => {
  const artifactId = safeKeyPart(
    sourceId || evalId || runId,
    EVAL_SOURCE_FIX_ARTIFACT_ID,
  );

  return {
    eventName: "onboarding_eval_source_fix_rerun_clicked",
    primaryPath: "evals",
    stage: "fix_eval_source",
    source: "eval_review_onboarding",
    artifactType: "eval_source_fix_route",
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      rerun_route: rerunRoute,
      route,
      run_id: runId,
      source_id: sourceId,
      source_type: sourceType,
      step: EVAL_FIX_STEP,
    }),
    idempotencyKey: [
      "onboarding_eval_source_fix_rerun_clicked",
      safeKeyPart(sourceType, "source"),
      safeKeyPart(evalId || sourceId || runId, "eval-source-fix"),
    ].join(":"),
    isSample: false,
  };
};

export const buildEvalFailuresReviewedPayload = ({
  evalId,
  evalLogId,
  reviewOutcome,
  reviewSurface = "usage_log_detail",
  rowSource,
  runId,
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
      step: EVAL_REVIEW_STEP,
      tab: "usage",
    }),
    idempotencyKey: [
      "eval_failures_reviewed",
      safeKeyPart(runId || evalLogId, "no-run"),
      safeKeyPart(evalId, "no-eval"),
    ].join(":"),
    isSample: false,
  };
};

export const buildEvalFixRerunReviewedPayload = ({
  evalId,
  evalLogId,
  previousRunId,
  rerunFrom,
  reviewOutcome,
  reviewSurface = "usage_log_detail",
  rowSource,
  runId,
  sourceId,
  sourceType,
} = {}) => {
  const normalizedRerunFrom = normalizeFixRerunOrigin(rerunFrom);
  const artifactId = safeKeyPart(
    runId || evalLogId || evalId,
    EVAL_REVIEW_ARTIFACT_ID,
  );

  return {
    eventName: "onboarding_eval_fix_rerun_reviewed",
    primaryPath: "evals",
    stage: "fix_eval_source",
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
      source_id: sourceId,
      source_type: sourceType,
      step: EVAL_REVIEW_STEP,
      tab: "usage",
    }),
    idempotencyKey: [
      "onboarding_eval_fix_rerun_reviewed",
      safeKeyPart(normalizedRerunFrom, "rerun"),
      safeKeyPart(previousRunId, "no-previous-run"),
      artifactId,
    ].join(":"),
    isSample: false,
  };
};

export const buildEvalFailureActionCreatedPayload = ({
  actionType,
  evalId,
  evalLogId,
  feedbackId,
  fixRoute,
  rowSource,
  runId,
  sourceId,
  sourceType,
  step,
} = {}) => {
  const artifactId = safeKeyPart(
    feedbackId || evalLogId || runId || evalId,
    EVAL_FIX_ARTIFACT_ID,
  );

  return {
    eventName: "eval_failure_action_created",
    primaryPath: "evals",
    stage: "fix_eval_source",
    source: "eval_review_onboarding",
    artifactType: "eval_feedback",
    artifactId,
    metadata: compactMetadata({
      action_type: actionType,
      eval_id: evalId,
      eval_log_id: evalLogId,
      feedback_id: feedbackId,
      fix_route: fixRoute,
      row_source: rowSource,
      run_id: runId,
      source_id: sourceId,
      source_type: sourceType,
      step: step || EVAL_FIX_STEP,
    }),
    idempotencyKey: [
      "eval_failure_action_created",
      safeKeyPart(feedbackId || evalLogId, "no-feedback"),
      safeKeyPart(evalId, "no-eval"),
    ].join(":"),
    isSample: false,
  };
};

export const buildEvalSourceFixCtaClickedPayload = ({
  evalId,
  evalLogId,
  fixRoute,
  rowSource,
  runId,
  sourceId,
  sourceType,
} = {}) => {
  const artifactId = safeKeyPart(
    sourceId || evalLogId || runId || evalId,
    EVAL_FIX_ARTIFACT_ID,
  );

  return {
    eventName: "onboarding_eval_source_fix_cta_clicked",
    primaryPath: "evals",
    stage: "fix_eval_source",
    source: "eval_review_onboarding",
    artifactType: "eval_source_fix_route",
    artifactId,
    metadata: compactMetadata({
      eval_id: evalId,
      eval_log_id: evalLogId,
      fix_route: fixRoute,
      row_source: rowSource,
      run_id: runId,
      source_id: sourceId,
      source_type: sourceType,
      step: EVAL_FIX_STEP,
    }),
    idempotencyKey: [
      "onboarding_eval_source_fix_cta_clicked",
      safeKeyPart(sourceId || evalLogId, "no-source"),
      safeKeyPart(evalId, "no-eval"),
    ].join(":"),
    isSample: false,
  };
};

export const buildEvalScorerEditCtaClickedPayload = ({
  editRoute,
  evalId,
  evalLogId,
  rowSource,
  runId,
  sourceId,
  sourceType,
} = {}) => {
  const artifactId = safeKeyPart(
    evalId || evalLogId || runId,
    "eval-scorer-edit",
  );

  return {
    eventName: "onboarding_eval_scorer_edit_cta_clicked",
    primaryPath: "evals",
    stage: "fix_eval_source",
    source: "eval_review_onboarding",
    artifactType: "eval_scorer",
    artifactId,
    metadata: compactMetadata({
      edit_route: editRoute,
      eval_id: evalId,
      eval_log_id: evalLogId,
      row_source: rowSource,
      run_id: runId,
      source_id: sourceId,
      source_type: sourceType,
      step: EVAL_CREATE_ONBOARDING_STEPS.SCORER,
    }),
    idempotencyKey: [
      "onboarding_eval_scorer_edit_cta_clicked",
      safeKeyPart(evalId, "no-eval"),
      safeKeyPart(evalLogId || runId, "no-run"),
    ].join(":"),
    isSample: false,
  };
};
