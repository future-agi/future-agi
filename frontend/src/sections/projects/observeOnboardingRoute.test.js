import { beforeEach, describe, expect, it } from "vitest";
import {
  buildObserveEvaluatorCreateHref,
  buildObserveProjectOnboardingHref,
  buildObserveRouteFocusPayload,
  buildObserveSetupHref,
  buildObserveTraceReviewHref,
  getObserveFirstTraceBaselineId,
  getFirstTraceIdFromTraceListResult,
  getObserveFirstTraceReviewTarget,
  getObserveOnboardingCopy,
  getObserveSetupInstallCommand,
  getObserveOnboardingParams,
  getObserveSetupPackageLabel,
  getObserveSetupOnboardingParams,
  getObserveTraceReviewOnboardingParams,
  persistObserveSetupIntent,
  readPersistedObserveSetupIntent,
  observeOnboardingStage,
  OBSERVE_SETUP_INTENT_STORAGE_KEY,
  OBSERVE_ONBOARDING_MODES,
  OBSERVE_ONBOARDING_SOURCES,
} from "./observeOnboardingRoute";

describe("observeOnboardingRoute", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("reads supported observe route params", () => {
    expect(
      getObserveOnboardingParams(
        "?source=onboarding&onboarding=send-first-trace",
      ),
    ).toEqual({
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
      setupLanguage: null,
      setupProvider: null,
      tourAnchor: null,
    });
  });

  it("reads observe project journey-step params from Home CTAs", () => {
    expect(
      getObserveOnboardingParams(
        "?tour_anchor=observe_send_trace_button&journey_step=send_first_trace",
      ),
    ).toEqual({
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
      setupLanguage: null,
      setupProvider: null,
      tourAnchor: "observe_send_trace_button",
    });

    expect(
      getObserveOnboardingParams(
        "?tour_anchor=observe_evaluator_button&journey_step=create_trace_evaluator",
      ),
    ).toEqual({
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.CREATE_EVALUATOR,
      setupLanguage: null,
      setupProvider: null,
      tourAnchor: "observe_evaluator_button",
    });
  });

  it("drops unsupported modes", () => {
    expect(
      getObserveOnboardingParams("?source=onboarding&onboarding=unknown"),
    ).toEqual({
      isOnboarding: true,
      mode: null,
      setupLanguage: null,
      setupProvider: null,
      tourAnchor: null,
    });
  });

  it("reads package intent on observe project params", () => {
    expect(
      getObserveOnboardingParams(
        "?source=onboarding&onboarding=send-first-trace&provider=Anthropic&language=TypeScript",
      ),
    ).toEqual({
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
      setupLanguage: "typescript",
      setupProvider: "anthropic",
      tourAnchor: null,
    });
  });

  it("reads package intent on observe project params from lifecycle email", () => {
    expect(
      getObserveOnboardingParams(
        "?source=onboarding_email&onboarding=send-first-trace&provider=Anthropic&language=TypeScript",
      ),
    ).toEqual({
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
      setupLanguage: "typescript",
      setupProvider: "anthropic",
      tourAnchor: null,
    });
  });

  it("reads observe setup onboarding route params", () => {
    expect(
      getObserveSetupOnboardingParams("?setup=true&source=onboarding"),
    ).toEqual({
      credentialStep: null,
      credentialsCopied: false,
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE,
      setupLanguage: null,
      setupProvider: null,
      source: OBSERVE_ONBOARDING_SOURCES.ONBOARDING,
      tourAnchor: null,
    });
  });

  it("reads observe setup journey-step params from Home CTAs", () => {
    expect(
      getObserveSetupOnboardingParams(
        "?tour_anchor=observe_create_project_button&journey_step=connect_observability",
      ),
    ).toEqual({
      credentialStep: null,
      credentialsCopied: false,
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE,
      setupLanguage: null,
      setupProvider: null,
      source: OBSERVE_ONBOARDING_SOURCES.ONBOARDING,
      tourAnchor: "observe_create_project_button",
    });
  });

  it("reads observe setup params after sample trace review", () => {
    expect(
      getObserveSetupOnboardingParams("?setup=true&source=sample_trace_review"),
    ).toEqual({
      credentialStep: null,
      credentialsCopied: false,
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE,
      setupLanguage: null,
      setupProvider: null,
      source: OBSERVE_ONBOARDING_SOURCES.SAMPLE_TRACE_REVIEW,
      tourAnchor: null,
    });
  });

  it("reads package intent on observe setup params", () => {
    expect(
      getObserveSetupOnboardingParams(
        "?setup=true&source=onboarding&provider=anthropic&language=typescript",
      ),
    ).toEqual({
      credentialStep: null,
      credentialsCopied: false,
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE,
      setupLanguage: "typescript",
      setupProvider: "anthropic",
      source: OBSERVE_ONBOARDING_SOURCES.ONBOARDING,
      tourAnchor: null,
    });
  });

  it("drops unsupported package intent on observe setup params", () => {
    expect(
      getObserveSetupOnboardingParams(
        "?setup=true&source=onboarding&provider=unknown&language=ruby",
      ),
    ).toMatchObject({
      setupLanguage: null,
      setupProvider: null,
    });
  });

  it("reads the returned-credentials setup state", () => {
    expect(
      getObserveSetupOnboardingParams(
        "?setup=true&source=onboarding&credential_step=done",
      ),
    ).toEqual({
      credentialStep: "done",
      credentialsCopied: true,
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE,
      setupLanguage: null,
      setupProvider: null,
      source: OBSERVE_ONBOARDING_SOURCES.ONBOARDING,
      tourAnchor: null,
    });
  });

  it("reads observe trace-review onboarding route params", () => {
    expect(
      getObserveTraceReviewOnboardingParams(
        "?source=onboarding&onboarding=review-first-trace",
      ),
    ).toEqual({
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE,
      setupLanguage: null,
      setupProvider: null,
      tourAnchor: null,
    });
  });

  it("reads trace-review package intent from lifecycle email", () => {
    expect(
      getObserveTraceReviewOnboardingParams(
        "?source=onboarding_email&onboarding=review-first-trace&provider=Anthropic&language=TypeScript",
      ),
    ).toEqual({
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE,
      setupLanguage: "typescript",
      setupProvider: "anthropic",
      tourAnchor: null,
    });
  });

  it("reads trace-review journey-step params from Home CTAs", () => {
    expect(
      getObserveTraceReviewOnboardingParams(
        "?tour_anchor=observe_trace_review_link&journey_step=review_first_trace",
      ),
    ).toEqual({
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE,
      setupLanguage: null,
      setupProvider: null,
      tourAnchor: "observe_trace_review_link",
    });
  });

  it("drops unsupported trace-review modes", () => {
    expect(
      getObserveTraceReviewOnboardingParams(
        "?source=onboarding&onboarding=create-evaluator",
      ),
    ).toEqual({
      isOnboarding: true,
      mode: null,
      setupLanguage: null,
      setupProvider: null,
      tourAnchor: null,
    });
  });

  it("builds observe setup hrefs with package intent", () => {
    expect(
      buildObserveSetupHref({
        credentialStep: "done",
        setupLanguage: "python",
        setupProvider: "anthropic",
      }),
    ).toBe(
      "/dashboard/observe?setup=true&source=onboarding&credential_step=done&provider=anthropic&language=python",
    );
  });

  it("builds observe project route-mode hrefs", () => {
    expect(
      buildObserveProjectOnboardingHref({
        observeId: "project-1",
        mode: OBSERVE_ONBOARDING_MODES.CREATE_EVALUATOR,
      }),
    ).toBe(
      "/dashboard/observe/project-1/llm-tracing?source=onboarding&onboarding=create-evaluator",
    );
  });

  it("opens the trace tab for the first-trace project route", () => {
    expect(
      buildObserveProjectOnboardingHref({
        observeId: "project-1",
        mode: OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
        setupLanguage: "python",
        setupProvider: "anthropic",
      }),
    ).toBe(
      "/dashboard/observe/project-1/llm-tracing?source=onboarding&onboarding=send-first-trace&selectedTab=trace&provider=anthropic&language=python",
    );
  });

  it("carries an existing trace baseline into the first-trace wait route", () => {
    const href = buildObserveProjectOnboardingHref({
      baselineTraceId: "trace-existing",
      observeId: "project-1",
      mode: OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
      setupLanguage: "python",
      setupProvider: "anthropic",
    });
    expect(href).toBe(
      "/dashboard/observe/project-1/llm-tracing?source=onboarding&onboarding=send-first-trace&selectedTab=trace&baseline_trace_id=trace-existing&provider=anthropic&language=python",
    );
    expect(getObserveFirstTraceBaselineId(href)).toBe("trace-existing");
  });

  it("builds observe trace review hrefs", () => {
    expect(
      buildObserveTraceReviewHref({
        observeId: "project-1",
        setupLanguage: "python",
        setupProvider: "anthropic",
        traceId: "trace-1",
      }),
    ).toBe(
      "/dashboard/observe/project-1/trace/trace-1?source=onboarding&onboarding=review-first-trace&provider=anthropic&language=python",
    );
  });

  it("builds eval creation hrefs from an observe project", () => {
    expect(
      buildObserveEvaluatorCreateHref({
        observeId: "project-1",
        quickStartAttribution: {
          quick_start_goal: "monitor_production_ai_app",
          quick_start_id: "observe",
          quick_start_primary_path: "observe",
        },
        setupLanguage: "python",
        setupProvider: "anthropic",
        traceId: "trace-1",
      }),
    ).toBe(
      "/dashboard/evaluations/create?source=onboarding&step=data&source_type=trace_project&source_id=project-1&trace_id=trace-1&provider=anthropic&language=python&quick_start_goal=monitor_production_ai_app&quick_start_id=observe&quick_start_primary_path=observe",
    );
  });

  it("selects the first trace review target once a trace is available", () => {
    expect(
      getObserveFirstTraceReviewTarget({
        activationObserveId: "project-1",
        activationTraceId: "trace-1",
        mode: OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
        observeId: "project-1",
      }),
    ).toEqual({ observeId: "project-1", traceId: "trace-1" });

    expect(
      getObserveFirstTraceReviewTarget({
        activationObserveId: "project-1",
        activationTraceId: "trace-1",
        loadedTraceId: "trace-2",
        mode: OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
        observeId: "project-1",
      }),
    ).toEqual({ observeId: "project-1", traceId: "trace-2" });

    expect(
      getObserveFirstTraceReviewTarget({
        activationObserveId: "project-2",
        activationTraceId: "trace-1",
        mode: OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
        observeId: "project-1",
      }),
    ).toBeNull();
  });

  it("reads the first trace id from trace list data", () => {
    expect(
      getFirstTraceIdFromTraceListResult({
        table: [
          { name: "missing id" },
          { trace_id: "trace-1" },
          { traceId: "trace-2" },
        ],
      }),
    ).toBe("trace-1");

    expect(
      getFirstTraceIdFromTraceListResult({
        table: [{ traceId: "trace-2" }],
      }),
    ).toBe("trace-2");

    expect(getFirstTraceIdFromTraceListResult({ table: [] })).toBeNull();
  });

  it("maps modes to activation stages", () => {
    expect(observeOnboardingStage(OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE)).toBe(
      "connect_observability",
    );
    expect(
      observeOnboardingStage(OBSERVE_ONBOARDING_MODES.CREATE_EVALUATOR),
    ).toBe("create_trace_evaluator");
    expect(
      observeOnboardingStage(OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE),
    ).toBe("review_first_trace");
    expect(
      observeOnboardingStage(OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE),
    ).toBe("waiting_for_first_trace");
  });

  it("returns mode-specific panel copy", () => {
    expect(
      getObserveOnboardingCopy(OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE),
    ).toMatchObject({
      currentStep: "Choose package",
      description:
        "Choose the package your app uses, paste the matching setup, run one request, wait for the trace, review it, then create the first quality check.",
      primaryLabel: "Choose package",
      title: "Connect Observe to your app",
    });
    expect(
      getObserveOnboardingCopy(OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE),
    ).toMatchObject({
      currentStep: "First trace",
      primaryLabel: "Check for trace",
      secondaryLabel: "Open package setup",
      title: "Send the first trace",
    });
    expect(
      getObserveOnboardingCopy(OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE),
    ).toMatchObject({
      currentStep: "Trace received",
      description:
        "Review this trace to inspect inputs, outputs, latency, cost, and errors. Next, create a quality check from it.",
      primaryLabel: "Review trace",
      title: "First trace received",
    });
    expect(
      getObserveOnboardingCopy(OBSERVE_ONBOARDING_MODES.CREATE_EVALUATOR),
    ).toMatchObject({
      currentStep: "Quality check",
      description:
        "Turn the reviewed trace into a repeatable quality check for future runs.",
      primaryLabel: "Create quality check",
      title: "Create a quality check",
    });
    expect(
      getObserveOnboardingCopy(OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE, {
        credentialsCopied: true,
      }),
    ).toMatchObject({
      currentStep: "Credentials ready",
      primaryLabel: "Paste keys and run trace",
      title: "Credentials copied",
    });
    expect(
      getObserveOnboardingCopy(OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE, {
        source: OBSERVE_ONBOARDING_SOURCES.SAMPLE_TRACE_REVIEW,
      }),
    ).toMatchObject({
      currentStep: "Real data",
      primaryLabel: "Send real trace",
      title: "Connect your app",
    });
  });

  it("returns package-specific Observe setup and trace-wait copy", () => {
    expect(
      getObserveSetupPackageLabel({
        setupLanguage: "TypeScript",
        setupProvider: "Anthropic",
      }),
    ).toBe("Anthropic TypeScript");
    expect(
      getObserveSetupInstallCommand({
        setupLanguage: "python",
        setupProvider: "Anthropic",
      }),
    ).toBe("pip install traceAI-anthropic anthropic");
    expect(
      getObserveSetupInstallCommand({
        setupLanguage: "typescript",
        setupProvider: "Anthropic",
      }),
    ).toBe(
      "npm install @traceai/fi-core @traceai/anthropic @opentelemetry/instrumentation @anthropic-ai/sdk",
    );
    expect(
      getObserveSetupInstallCommand({
        setupLanguage: "typescript",
        setupProvider: "bedrock",
      }),
    ).toBe("");

    expect(
      getObserveOnboardingCopy(OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE, {
        setupLanguage: "typescript",
        setupProvider: "anthropic",
      }),
    ).toMatchObject({
      currentStep: "Anthropic setup",
      description:
        "Use the Anthropic TypeScript setup below, run one request, and keep this page open while we wait for the trace. After review, the next step is the first quality check.",
      primaryLabel: "Open Anthropic setup",
      title: "Connect Anthropic TypeScript",
    });

    expect(
      getObserveOnboardingCopy(OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE, {
        setupLanguage: "typescript",
        setupProvider: "anthropic",
      }),
    ).toMatchObject({
      currentStep: "Anthropic trace",
      description:
        "Keep this page open, run one Anthropic TypeScript request from your app, and Future AGI will open the trace when it appears. After review, the next step is the first quality check.",
      primaryLabel: "Check for Anthropic TypeScript trace",
      secondaryLabel: "Open Anthropic setup",
      title: "Send the first trace",
    });

    expect(
      getObserveOnboardingCopy(OBSERVE_ONBOARDING_MODES.REVIEW_FIRST_TRACE, {
        setupLanguage: "typescript",
        setupProvider: "anthropic",
      }),
    ).toMatchObject({
      currentStep: "Anthropic trace",
      description:
        "Review this Anthropic TypeScript trace to inspect inputs, outputs, latency, cost, and errors. Next, create a quality check from it.",
      primaryLabel: "Review trace",
    });

    expect(
      getObserveOnboardingCopy(OBSERVE_ONBOARDING_MODES.CREATE_EVALUATOR, {
        setupLanguage: "typescript",
        setupProvider: "anthropic",
      }),
    ).toMatchObject({
      description:
        "Turn the reviewed Anthropic TypeScript trace into a repeatable quality check for future runs.",
      primaryLabel: "Create quality check",
    });

    expect(
      getObserveOnboardingCopy(OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE, {
        credentialsCopied: true,
        setupLanguage: "typescript",
        setupProvider: "anthropic",
      }),
    ).toMatchObject({
      description:
        "Paste both copied values into the Anthropic TypeScript setup snippet, then run one request. After the trace arrives, review it and create the first quality check.",
      primaryLabel: "Run Anthropic request",
    });
  });

  it("persists sanitized Observe setup package intent", () => {
    expect(
      persistObserveSetupIntent({
        setupLanguage: "TypeScript",
        setupProvider: "Anthropic",
      }),
    ).toEqual({
      setupLanguage: "typescript",
      setupProvider: "anthropic",
    });
    expect(readPersistedObserveSetupIntent()).toEqual({
      setupLanguage: "typescript",
      setupProvider: "anthropic",
    });

    window.sessionStorage.setItem(OBSERVE_SETUP_INTENT_STORAGE_KEY, "{");
    expect(readPersistedObserveSetupIntent()).toEqual({});
    expect(
      window.sessionStorage.getItem(OBSERVE_SETUP_INTENT_STORAGE_KEY),
    ).toBeNull();
  });

  it("does not keep language-only package intent", () => {
    expect(
      getObserveSetupPackageLabel({
        setupLanguage: "python",
      }),
    ).toBe("");
    expect(
      persistObserveSetupIntent({
        setupLanguage: "python",
      }),
    ).toEqual({});
    expect(
      window.sessionStorage.getItem(OBSERVE_SETUP_INTENT_STORAGE_KEY),
    ).toBeNull();
  });

  it("builds a safe route-focus payload", () => {
    expect(
      buildObserveRouteFocusPayload({
        observeId: "project-1",
        mode: OBSERVE_ONBOARDING_MODES.CREATE_EVALUATOR,
        setupLanguage: "typescript",
        setupProvider: "anthropic",
      }),
    ).toMatchObject({
      eventName: "onboarding_observe_route_focus_viewed",
      primaryPath: "observe",
      stage: "create_trace_evaluator",
      source: "observe_project_onboarding",
      artifactType: "observe_project",
      artifactId: "project-1",
      projectId: "project-1",
      metadata: {
        project_id: "project-1",
        route_mode: "create-evaluator",
        setup_language: "typescript",
        setup_provider: "anthropic",
      },
      idempotencyKey:
        "onboarding_observe_route_focus_viewed:anthropic:typescript:create-evaluator:project-1",
      isSample: false,
    });
  });

  it("builds a safe setup route-focus payload", () => {
    expect(
      buildObserveRouteFocusPayload({
        credentialStep: "done",
        mode: OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE,
        setupLanguage: "typescript",
        setupProvider: "anthropic",
      }),
    ).toMatchObject({
      eventName: "onboarding_observe_route_focus_viewed",
      primaryPath: "observe",
      stage: "connect_observability",
      source: "observe_setup_onboarding",
      artifactType: "observe_setup",
      artifactId: "observe-setup",
      metadata: {
        credential_step: "done",
        route_mode: "setup-observe",
        setup_language: "typescript",
        setup_provider: "anthropic",
        setup: true,
      },
      idempotencyKey:
        "onboarding_observe_route_focus_viewed:done:anthropic:typescript:setup-observe:observe-setup",
      isSample: false,
    });
  });

  it("builds a distinct setup focus payload after sample trace review", () => {
    expect(
      buildObserveRouteFocusPayload({
        mode: OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE,
        setupSource: OBSERVE_ONBOARDING_SOURCES.SAMPLE_TRACE_REVIEW,
      }),
    ).toMatchObject({
      eventName: "onboarding_observe_route_focus_viewed",
      primaryPath: "observe",
      stage: "connect_real_data",
      source: OBSERVE_ONBOARDING_SOURCES.SAMPLE_TRACE_REVIEW,
      artifactType: "observe_setup",
      artifactId: "observe-setup",
      metadata: {
        route_mode: "setup-observe",
        setup: true,
        setup_source: OBSERVE_ONBOARDING_SOURCES.SAMPLE_TRACE_REVIEW,
      },
      idempotencyKey:
        "onboarding_observe_route_focus_viewed:sample_trace_review:setup-observe:observe-setup",
      isSample: false,
    });
  });
});
