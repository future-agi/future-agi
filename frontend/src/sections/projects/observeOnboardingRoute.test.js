import { describe, expect, it } from "vitest";
import {
  buildObserveEvaluatorCreateHref,
  buildObserveProjectOnboardingHref,
  buildObserveRouteFocusPayload,
  buildObserveTraceReviewHref,
  getObserveOnboardingCopy,
  getObserveOnboardingParams,
  getObserveSetupOnboardingParams,
  getObserveTraceReviewOnboardingParams,
  observeOnboardingStage,
  OBSERVE_ONBOARDING_MODES,
  OBSERVE_ONBOARDING_SOURCES,
} from "./observeOnboardingRoute";

describe("observeOnboardingRoute", () => {
  it("reads supported observe route params", () => {
    expect(
      getObserveOnboardingParams(
        "?source=onboarding&onboarding=send-first-trace",
      ),
    ).toEqual({
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE,
    });
  });

  it("drops unsupported modes", () => {
    expect(
      getObserveOnboardingParams("?source=onboarding&onboarding=unknown"),
    ).toEqual({
      isOnboarding: true,
      mode: null,
    });
  });

  it("reads observe setup onboarding route params", () => {
    expect(
      getObserveSetupOnboardingParams("?setup=true&source=onboarding"),
    ).toEqual({
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE,
      source: OBSERVE_ONBOARDING_SOURCES.ONBOARDING,
    });
  });

  it("reads observe setup params after sample trace review", () => {
    expect(
      getObserveSetupOnboardingParams("?setup=true&source=sample_trace_review"),
    ).toEqual({
      isOnboarding: true,
      mode: OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE,
      source: OBSERVE_ONBOARDING_SOURCES.SAMPLE_TRACE_REVIEW,
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
    });
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

  it("builds observe trace review hrefs", () => {
    expect(
      buildObserveTraceReviewHref({
        observeId: "project-1",
        traceId: "trace-1",
      }),
    ).toBe(
      "/dashboard/observe/project-1/trace/trace-1?source=onboarding&onboarding=review-first-trace",
    );
  });

  it("builds eval creation hrefs from an observe project", () => {
    expect(buildObserveEvaluatorCreateHref({ observeId: "project-1" })).toBe(
      "/dashboard/evaluations/create?source=onboarding&step=data&source_type=trace_project&source_id=project-1",
    );
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
      currentStep: "Setup",
      primaryLabel: "Review setup",
      title: "Connect Observe to your app",
    });
    expect(
      getObserveOnboardingCopy(OBSERVE_ONBOARDING_MODES.SEND_FIRST_TRACE),
    ).toMatchObject({
      currentStep: "First trace",
      primaryLabel: "Refresh traces",
      title: "Send the first trace",
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

  it("builds a safe route-focus payload", () => {
    expect(
      buildObserveRouteFocusPayload({
        observeId: "project-1",
        mode: OBSERVE_ONBOARDING_MODES.CREATE_EVALUATOR,
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
      },
      idempotencyKey:
        "onboarding_observe_route_focus_viewed:create-evaluator:project-1",
      isSample: false,
    });
  });

  it("builds a safe setup route-focus payload", () => {
    expect(
      buildObserveRouteFocusPayload({
        mode: OBSERVE_ONBOARDING_MODES.SETUP_OBSERVE,
      }),
    ).toMatchObject({
      eventName: "onboarding_observe_route_focus_viewed",
      primaryPath: "observe",
      stage: "connect_observability",
      source: "observe_setup_onboarding",
      artifactType: "observe_setup",
      artifactId: "observe-setup",
      metadata: {
        route_mode: "setup-observe",
        setup: true,
      },
      idempotencyKey:
        "onboarding_observe_route_focus_viewed:setup-observe:observe-setup",
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
