const DEFAULT_ARTIFACT_ID = "observe-onboarding";

export const OBSERVE_FIRST_TRACE_LOADED_EVENT = "observe-first-trace-loaded";

export const OBSERVE_ONBOARDING_MODES = {
  CREATE_EVALUATOR: "create-evaluator",
  REVIEW_FIRST_TRACE: "review-first-trace",
  SEND_FIRST_TRACE: "send-first-trace",
  SETUP_OBSERVE: "setup-observe",
};

export const OBSERVE_ONBOARDING_SOURCES = {
  ONBOARDING: "onboarding",
  SAMPLE_TRACE_REVIEW: "sample_trace_review",
};

export const OBSERVE_ONBOARDING_CREDENTIAL_STEPS = {
  DONE: "done",
};

const projectModeSet = new Set([
  OBSERVE_ONBOARDING_MODES.CREATE_EVALUATOR,
  OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
]);
const routeFocusModeSet = new Set([
  OBSERVE_ONBOARDING_MODES.CREATE_EVALUATOR,
  OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
  OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE,
]);
const setupSourceSet = new Set(Object.values(OBSERVE_ONBOARDING_SOURCES));
const journeyStepMode = {
  connect_observability: OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE,
  create_trace_evaluator: OBSERVE_ONBOARDING_MODES.CREATE_EVALUATOR,
  review_first_trace: OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE,
  send_first_trace: OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
};

const safeKeyPart = (value, fallback = DEFAULT_ARTIFACT_ID) =>
  String(value || fallback)
    .replace(/[^A-Za-z0-9_.:-]/g, "-")
    .slice(0, 64);

const compactMetadata = (value = {}) =>
  Object.fromEntries(
    Object.entries(value).filter(
      ([, item]) => item !== undefined && item !== null && item !== "",
    ),
  );

export const getObserveOnboardingParams = (search = "") => {
  const params = new URLSearchParams(search);
  const journeyMode = journeyStepMode[params.get("journey_step")] || null;
  const isOnboarding =
    params.get("source") === OBSERVE_ONBOARDING_SOURCES.ONBOARDING ||
    Boolean(journeyMode);
  const rawMode = params.get("onboarding");
  const mode = projectModeSet.has(rawMode)
    ? rawMode
    : projectModeSet.has(journeyMode)
      ? journeyMode
      : null;
  return {
    isOnboarding,
    mode: isOnboarding ? mode : null,
    tourAnchor: params.get("tour_anchor"),
  };
};

export const getObserveSetupOnboardingParams = (search = "") => {
  const params = new URLSearchParams(search);
  const source = params.get("source");
  const credentialStep = params.get("credential_step");
  const journeyMode = journeyStepMode[params.get("journey_step")] || null;
  const isSetupJourney = journeyMode === OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE;
  const isOnboarding = setupSourceSet.has(source) || isSetupJourney;
  const isSetupRoute =
    params.get("setup") === "true" ||
    params.get("onboarding") === OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE ||
    isSetupJourney;
  return {
    isOnboarding,
    mode:
      isOnboarding && isSetupRoute
        ? OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE
        : null,
    source: setupSourceSet.has(source)
      ? source
      : isSetupJourney
        ? OBSERVE_ONBOARDING_SOURCES.ONBOARDING
        : null,
    credentialStep: isOnboarding ? credentialStep : null,
    credentialsCopied:
      isOnboarding &&
      credentialStep === OBSERVE_ONBOARDING_CREDENTIAL_STEPS.DONE,
    tourAnchor: params.get("tour_anchor"),
  };
};

export const getObserveTraceReviewOnboardingParams = (search = "") => {
  const params = new URLSearchParams(search);
  const journeyMode = journeyStepMode[params.get("journey_step")] || null;
  const isOnboarding =
    params.get("source") === OBSERVE_ONBOARDING_SOURCES.ONBOARDING ||
    journeyMode === OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE;
  const rawMode = params.get("onboarding");
  const mode =
    rawMode === OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE
      ? rawMode
      : journeyMode === OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE
        ? journeyMode
        : null;
  return {
    isOnboarding,
    mode: isOnboarding ? mode : null,
    tourAnchor: params.get("tour_anchor"),
  };
};

export const observeOnboardingStage = (mode) => {
  if (mode === OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE) {
    return "connect_observability";
  }
  if (mode === OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE) {
    return "review_first_trace";
  }
  if (mode === OBSERVE_ONBOARDING_MODES.CREATE_EVALUATOR) {
    return "create_trace_evaluator";
  }
  return "waiting_for_first_trace";
};

export const getObserveOnboardingCopy = (
  mode,
  { credentialsCopied, source } = {},
) => {
  if (mode === OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE) {
    if (source === OBSERVE_ONBOARDING_SOURCES.SAMPLE_TRACE_REVIEW) {
      return {
        currentStep: "Real data",
        description:
          "Use the setup below to send one real or test trace from your app.",
        primaryLabel: "Send real trace",
        secondaryLabel: null,
        steps: [
          { label: "Sample review", complete: true },
          { label: "Install", complete: false },
          { label: "Trace", complete: false },
        ],
        title: "Connect your app",
      };
    }

    if (credentialsCopied) {
      return {
        currentStep: "Credentials ready",
        description:
          "Paste both copied values into the setup snippet, then run one real or test request.",
        primaryLabel: "Paste keys and run trace",
        secondaryLabel: null,
        steps: [
          { label: "Keys", complete: true },
          { label: "Trace", complete: false },
          { label: "Review", complete: false },
        ],
        title: "Credentials copied",
      };
    }

    return {
      currentStep: "Setup",
      description:
        "Install tracing, load your keys, and send one real or test request.",
      primaryLabel: "Review setup",
      secondaryLabel: null,
      steps: [
        { label: "Install", complete: false },
        { label: "Trace", complete: false },
        { label: "Review", complete: false },
      ],
      title: "Connect Observe to your app",
    };
  }

  if (mode === OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE) {
    return {
      currentStep: "First trace",
      description:
        "Send one production or test trace to unlock the first review step.",
      primaryLabel: "Open setup",
      secondaryLabel: "Refresh traces",
      steps: [
        { label: "Project", complete: true },
        { label: "Trace", complete: false },
        { label: "Review", complete: false },
      ],
      title: "Send the first trace",
    };
  }

  if (mode === OBSERVE_ONBOARDING_MODES.CREATE_EVALUATOR) {
    return {
      currentStep: "Evaluator",
      description:
        "Turn the reviewed trace into a repeatable quality check for future runs.",
      primaryLabel: "Create evaluator",
      secondaryLabel: "Refresh traces",
      steps: [
        { label: "Project", complete: true },
        { label: "Trace review", complete: true },
        { label: "Evaluator", complete: false },
      ],
      title: "Create an evaluator",
    };
  }

  if (mode === OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE) {
    return {
      currentStep: "Trace received",
      description:
        "Review it now to understand latency, cost, and quality context.",
      primaryLabel: "Review trace",
      secondaryLabel: "Refresh traces",
      steps: [
        { label: "Project", complete: true },
        { label: "Trace", complete: true },
        { label: "Review", complete: false },
      ],
      title: "First trace received",
    };
  }

  return null;
};

export const buildObserveProjectOnboardingHref = ({ observeId, mode } = {}) => {
  if (!observeId) return "/dashboard/observe";
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  if (projectModeSet.has(mode)) params.set("onboarding", mode);
  if (mode === OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE) {
    params.set("selectedTab", "trace");
  }
  return `/dashboard/observe/${observeId}/llm-tracing?${params.toString()}`;
};

export const buildObserveTraceReviewHref = ({ observeId, traceId } = {}) => {
  if (!observeId || !traceId) return "/dashboard/observe";
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("onboarding", OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE);
  return `/dashboard/observe/${observeId}/trace/${traceId}?${params.toString()}`;
};

export const buildObserveEvaluatorCreateHref = ({ observeId } = {}) => {
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("step", "data");
  params.set("source_type", "trace_project");
  if (observeId) params.set("source_id", observeId);
  return `/dashboard/evaluations/create?${params.toString()}`;
};

export const getObserveFirstTraceReviewTarget = ({
  activationObserveId,
  activationTraceId,
  loadedTraceId,
  mode,
  observeId,
} = {}) => {
  if (mode !== OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE) return null;
  if (!observeId) return null;

  const resolvedObserveId = activationObserveId || observeId;
  if (String(resolvedObserveId) !== String(observeId)) return null;

  const traceId = loadedTraceId || activationTraceId;
  if (!traceId) return null;

  return { observeId, traceId };
};

export const getFirstTraceIdFromTraceListResult = (result = {}) => {
  const rows = Array.isArray(result?.table) ? result.table : [];
  const firstTrace = rows.find((row) => row?.trace_id || row?.traceId);
  return firstTrace?.trace_id || firstTrace?.traceId || null;
};

export const buildObserveRouteFocusPayload = ({
  credentialStep,
  observeId,
  mode,
  setupSource,
} = {}) => {
  const normalizedMode = routeFocusModeSet.has(mode)
    ? mode
    : OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE;
  const isSetupMode = normalizedMode === OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE;
  const isSampleReviewSetup =
    isSetupMode &&
    setupSource === OBSERVE_ONBOARDING_SOURCES.SAMPLE_TRACE_REVIEW;
  const artifactId = isSetupMode
    ? "observe-setup"
    : safeKeyPart(observeId, DEFAULT_ARTIFACT_ID);

  return {
    eventName: "onboarding_observe_route_focus_viewed",
    primaryPath: "observe",
    stage: isSampleReviewSetup
      ? "connect_real_data"
      : observeOnboardingStage(normalizedMode),
    source: isSampleReviewSetup
      ? OBSERVE_ONBOARDING_SOURCES.SAMPLE_TRACE_REVIEW
      : isSetupMode
        ? "observe_setup_onboarding"
        : "observe_project_onboarding",
    artifactType: isSetupMode ? "observe_setup" : "observe_project",
    artifactId,
    projectId: isSetupMode ? undefined : observeId,
    metadata: compactMetadata({
      project_id: isSetupMode ? undefined : observeId,
      credential_step: isSetupMode ? credentialStep : undefined,
      route_mode: normalizedMode,
      setup_source: isSampleReviewSetup ? setupSource : undefined,
      setup: isSetupMode ? true : undefined,
    }),
    idempotencyKey: [
      "onboarding_observe_route_focus_viewed",
      isSampleReviewSetup ? setupSource : undefined,
      isSetupMode ? credentialStep : undefined,
      safeKeyPart(normalizedMode, "mode"),
      artifactId,
    ]
      .filter(Boolean)
      .join(":"),
    isSample: false,
  };
};
