import { appendSetupQuickStartAttributionToHref } from "src/sections/auth/jwt/setup-org-quick-starts";
import {
  getObservePackageInstallCommand,
  getObserveSetupPackageLabel as getCatalogObserveSetupPackageLabel,
  getObserveSetupProviderLabel as getCatalogObserveSetupProviderLabel,
  normalizeObserveSetupLanguage,
  normalizeObserveSetupProvider,
} from "./observeSetupCatalog";

const DEFAULT_ARTIFACT_ID = "observe-onboarding";

export const OBSERVE_FIRST_TRACE_LOADED_EVENT = "observe-first-trace-loaded";
export const OBSERVE_SETUP_INTENT_STORAGE_KEY =
  "futureagi.observe_setup_intent";

export const OBSERVE_ONBOARDING_MODES = {
  CREATE_EVALUATOR: "create-evaluator",
  REVIEW_FIRST_TRACE: "review-first-trace",
  SEND_FIRST_TRACE: "send-first-trace",
  SETUP_OBSERVE: "setup-observe",
};

export const OBSERVE_ONBOARDING_SOURCES = {
  ONBOARDING: "onboarding",
  ONBOARDING_EMAIL: "onboarding_email",
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

const safeSetupProvider = normalizeObserveSetupProvider;
const safeSetupLanguage = (value) =>
  normalizeObserveSetupLanguage(value) || null;

const setupIntentFromParams = (params) => ({
  setupProvider: safeSetupProvider(
    params.get("provider") || params.get("package") || params.get("instrument"),
  ),
  setupLanguage: safeSetupLanguage(
    params.get("language") || params.get("lang"),
  ),
});

const appendSetupIntentParams = (
  params,
  { setupLanguage, setupProvider } = {},
) => {
  const safeProvider = safeSetupProvider(setupProvider);
  const safeLanguage = safeSetupLanguage(setupLanguage);
  if (safeProvider) params.set("provider", safeProvider);
  if (safeLanguage) params.set("language", safeLanguage);
};

const appendQuickStartAttribution = (href, { quickStartAttribution, search }) =>
  appendSetupQuickStartAttributionToHref(
    href,
    quickStartAttribution ||
      Object.fromEntries(toSearchParams(search).entries()),
  );

const toSearchParams = (search = "") =>
  search instanceof URLSearchParams
    ? new URLSearchParams(search)
    : new URLSearchParams(search);

export const getObserveSetupPackageLabel = ({
  setupLanguage,
  setupProvider,
} = {}) => getCatalogObserveSetupPackageLabel({ setupLanguage, setupProvider });

export const getObserveSetupInstallCommand = ({
  setupLanguage,
  setupProvider,
} = {}) => getObservePackageInstallCommand({ setupLanguage, setupProvider });

export const getObserveFirstTraceBaselineId = (search = "") => {
  const value = toSearchParams(search).get("baseline_trace_id");
  return value ? value.slice(0, 256) : null;
};

const getObserveSetupProviderLabel = (setupProvider) =>
  getCatalogObserveSetupProviderLabel(setupProvider);

export const normalizeObserveSetupIntent = ({
  setupLanguage,
  setupProvider,
} = {}) => ({
  setupProvider: safeSetupProvider(setupProvider),
  setupLanguage: safeSetupProvider(setupProvider)
    ? safeSetupLanguage(setupLanguage)
    : null,
});

export const persistObserveSetupIntent = (input = {}) => {
  const intent = normalizeObserveSetupIntent(input);
  if (!intent.setupLanguage && !intent.setupProvider) {
    try {
      window.sessionStorage?.removeItem(OBSERVE_SETUP_INTENT_STORAGE_KEY);
    } catch {
      // Setup should continue even if browser storage is unavailable.
    }
    return {};
  }

  try {
    window.sessionStorage?.setItem(
      OBSERVE_SETUP_INTENT_STORAGE_KEY,
      JSON.stringify(intent),
    );
  } catch {
    // Setup should continue even if browser storage is unavailable.
  }
  return intent;
};

export const readPersistedObserveSetupIntent = () => {
  try {
    const rawValue = window.sessionStorage?.getItem(
      OBSERVE_SETUP_INTENT_STORAGE_KEY,
    );
    if (!rawValue) return {};

    const parsedValue = JSON.parse(rawValue);
    const intent = normalizeObserveSetupIntent(parsedValue);
    if (!intent.setupLanguage && !intent.setupProvider) {
      window.sessionStorage?.removeItem(OBSERVE_SETUP_INTENT_STORAGE_KEY);
      return {};
    }
    return intent;
  } catch {
    try {
      window.sessionStorage?.removeItem(OBSERVE_SETUP_INTENT_STORAGE_KEY);
    } catch {
      // Ignore cleanup failures.
    }
    return {};
  }
};

export const getObserveOnboardingParams = (search = "") => {
  const params = new URLSearchParams(search);
  const { setupLanguage, setupProvider } = setupIntentFromParams(params);
  const journeyMode = journeyStepMode[params.get("journey_step")] || null;
  const isOnboarding =
    params.get("source") === OBSERVE_ONBOARDING_SOURCES.ONBOARDING ||
    params.get("source") === OBSERVE_ONBOARDING_SOURCES.ONBOARDING_EMAIL ||
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
    setupLanguage: isOnboarding ? setupLanguage : null,
    setupProvider: isOnboarding ? setupProvider : null,
    tourAnchor: params.get("tour_anchor"),
  };
};

export const getObserveSetupOnboardingParams = (search = "") => {
  const params = new URLSearchParams(search);
  const source = params.get("source");
  const credentialStep = params.get("credential_step");
  const { setupLanguage, setupProvider } = setupIntentFromParams(params);
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
    setupLanguage: isOnboarding ? setupLanguage : null,
    setupProvider: isOnboarding ? setupProvider : null,
    tourAnchor: params.get("tour_anchor"),
  };
};

export const getObserveTraceReviewOnboardingParams = (search = "") => {
  const params = new URLSearchParams(search);
  const { setupLanguage, setupProvider } = setupIntentFromParams(params);
  const journeyMode = journeyStepMode[params.get("journey_step")] || null;
  const isOnboarding =
    params.get("source") === OBSERVE_ONBOARDING_SOURCES.ONBOARDING ||
    params.get("source") === OBSERVE_ONBOARDING_SOURCES.ONBOARDING_EMAIL ||
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
    setupLanguage: isOnboarding ? setupLanguage : null,
    setupProvider: isOnboarding ? setupProvider : null,
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
  { credentialsCopied, setupLanguage, setupProvider, source } = {},
) => {
  const setupPackageLabel = getObserveSetupPackageLabel({
    setupLanguage,
    setupProvider,
  });
  const setupProviderLabel = getObserveSetupProviderLabel(setupProvider);
  const packageSetupDescription = setupPackageLabel
    ? `Use the ${setupPackageLabel} setup below, run one request, and keep this page open while we wait for the trace. After review, the next step is the first quality check.`
    : null;

  if (mode === OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE) {
    if (source === OBSERVE_ONBOARDING_SOURCES.SAMPLE_TRACE_REVIEW) {
      return {
        currentStep: setupProviderLabel
          ? `${setupProviderLabel} setup`
          : "Real data",
        description:
          packageSetupDescription ||
          "Use the setup below to send one real or test trace from your app.",
        primaryLabel: setupProviderLabel
          ? `Send ${setupProviderLabel} trace`
          : "Send real trace",
        secondaryLabel: null,
        steps: [
          { label: "Sample review", complete: true },
          { label: "Install", complete: false },
          { label: "Trace", complete: false },
        ],
        title: setupPackageLabel
          ? `Connect ${setupPackageLabel}`
          : "Connect your app",
      };
    }

    if (credentialsCopied) {
      return {
        currentStep: "Credentials ready",
        description: setupPackageLabel
          ? `Paste both copied values into the ${setupPackageLabel} setup snippet, then run one request. After the trace arrives, review it and create the first quality check.`
          : "Paste both copied values into the setup snippet, then run one real or test request. After the trace arrives, review it and create the first quality check.",
        primaryLabel: setupProviderLabel
          ? `Run ${setupProviderLabel} request`
          : "Paste keys and run trace",
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
      currentStep: setupProviderLabel
        ? `${setupProviderLabel} setup`
        : "Choose package",
      description:
        packageSetupDescription ||
        "Choose the package your app uses, paste the matching setup, run one request, wait for the trace, review it, then create the first quality check.",
      primaryLabel: setupProviderLabel
        ? `Open ${setupProviderLabel} setup`
        : "Choose package",
      secondaryLabel: null,
      steps: [
        { label: "Install", complete: false },
        { label: "Trace", complete: false },
        { label: "Review", complete: false },
      ],
      title: setupPackageLabel
        ? `Connect ${setupPackageLabel}`
        : "Connect Observe to your app",
    };
  }

  if (mode === OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE) {
    return {
      currentStep: setupProviderLabel
        ? `${setupProviderLabel} trace`
        : "First trace",
      description: setupPackageLabel
        ? `Keep this page open, run one ${setupPackageLabel} request from your app, and Future AGI will open the trace when it appears. After review, the next step is the first quality check.`
        : "Keep this page open, run one production or test request, and Future AGI will open the trace when it appears. After review, the next step is the first quality check.",
      primaryLabel: setupPackageLabel
        ? `Check for ${setupPackageLabel} trace`
        : "Check for trace",
      secondaryLabel: setupProviderLabel
        ? `Open ${setupProviderLabel} setup`
        : "Open package setup",
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
      currentStep: "Quality check",
      description: setupPackageLabel
        ? `Turn the reviewed ${setupPackageLabel} trace into a repeatable quality check for future runs.`
        : "Turn the reviewed trace into a repeatable quality check for future runs.",
      primaryLabel: "Create quality check",
      secondaryLabel: "Check traces",
      steps: [
        { label: "Project", complete: true },
        { label: "Trace review", complete: true },
        { label: "Quality check", complete: false },
      ],
      title: "Create a quality check",
    };
  }

  if (mode === OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE) {
    return {
      currentStep: setupProviderLabel
        ? `${setupProviderLabel} trace`
        : "Trace received",
      description: setupPackageLabel
        ? `Review this ${setupPackageLabel} trace to inspect inputs, outputs, latency, cost, and errors. Next, create a quality check from it.`
        : "Review this trace to inspect inputs, outputs, latency, cost, and errors. Next, create a quality check from it.",
      primaryLabel: "Review trace",
      secondaryLabel: "Check traces",
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

export const buildObserveSetupHref = ({
  credentialStep,
  quickStartAttribution,
  search,
  setupLanguage,
  setupProvider,
  source = OBSERVE_ONBOARDING_SOURCES.ONBOARDING,
} = {}) => {
  const params = new URLSearchParams();
  params.set("setup", "true");
  params.set("source", source);
  if (credentialStep) params.set("credential_step", credentialStep);
  appendSetupIntentParams(params, { setupLanguage, setupProvider });
  return appendQuickStartAttribution(
    `/dashboard/observe?${params.toString()}`,
    {
      quickStartAttribution,
      search,
    },
  );
};

export const buildObserveProjectOnboardingHref = ({
  baselineTraceId,
  observeId,
  mode,
  quickStartAttribution,
  search,
  setupLanguage,
  setupProvider,
} = {}) => {
  if (!observeId) return "/dashboard/observe";
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  if (projectModeSet.has(mode)) params.set("onboarding", mode);
  if (mode === OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE) {
    params.set("selectedTab", "trace");
    if (baselineTraceId) params.set("baseline_trace_id", baselineTraceId);
  }
  appendSetupIntentParams(params, { setupLanguage, setupProvider });
  return appendQuickStartAttribution(
    `/dashboard/observe/${observeId}/llm-tracing?${params.toString()}`,
    { quickStartAttribution, search },
  );
};

export const buildObserveTraceReviewHref = ({
  observeId,
  quickStartAttribution,
  search,
  setupLanguage,
  setupProvider,
  traceId,
} = {}) => {
  if (!observeId || !traceId) return "/dashboard/observe";
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("onboarding", OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE);
  appendSetupIntentParams(params, { setupLanguage, setupProvider });
  return appendQuickStartAttribution(
    `/dashboard/observe/${observeId}/trace/${traceId}?${params.toString()}`,
    { quickStartAttribution, search },
  );
};

export const buildObserveEvaluatorCreateHref = ({
  observeId,
  quickStartAttribution,
  search,
  setupLanguage,
  setupProvider,
  traceId,
} = {}) => {
  const params = new URLSearchParams();
  params.set("source", "onboarding");
  params.set("step", "data");
  params.set("source_type", "trace_project");
  if (observeId) params.set("source_id", observeId);
  if (traceId) params.set("trace_id", traceId);
  appendSetupIntentParams(params, { setupLanguage, setupProvider });
  return appendQuickStartAttribution(
    `/dashboard/evaluations/create?${params.toString()}`,
    { quickStartAttribution, search },
  );
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
  setupLanguage,
  setupProvider,
  setupSource,
} = {}) => {
  const normalizedSetupLanguage = safeSetupLanguage(setupLanguage);
  const normalizedSetupProvider = safeSetupProvider(setupProvider);
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
      setup_language: normalizedSetupLanguage || undefined,
      setup_provider: normalizedSetupProvider || undefined,
      setup_source: isSampleReviewSetup ? setupSource : undefined,
      setup: isSetupMode ? true : undefined,
    }),
    idempotencyKey: [
      "onboarding_observe_route_focus_viewed",
      isSampleReviewSetup ? setupSource : undefined,
      isSetupMode ? credentialStep : undefined,
      normalizedSetupProvider,
      normalizedSetupLanguage,
      safeKeyPart(normalizedMode, "mode"),
      artifactId,
    ]
      .filter(Boolean)
      .join(":"),
    isSample: false,
  };
};
