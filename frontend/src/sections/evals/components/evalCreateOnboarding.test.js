import { describe, expect, it } from "vitest";
import {
  buildEvalCreateDraftHref,
  buildEvalFailureActionCreatedPayload,
  buildEvalFailuresReviewedPayload,
  buildEvalReviewDetailHref,
  buildEvalReviewRouteFocusPayload,
  buildEvalRouteFocusPayload,
  buildEvalRunCompletedPayload,
  buildEvalScorerCreatedPayload,
  buildEvalSourceSelectedPayload,
  EVAL_CREATE_ONBOARDING_STEPS,
  EVAL_CREATE_SOURCE_TABS,
  evalCreateOnboardingStage,
  getEvalCreateInitialSourceTab,
  getEvalCreateOnboardingCopy,
  getEvalCreateOnboardingParams,
  getEvalFailureActionOnboardingParams,
  getEvalReviewOnboardingCopy,
  getEvalReviewOnboardingParams,
} from "./evalCreateOnboarding";

describe("evalCreateOnboarding", () => {
  it("parses eval create onboarding query params", () => {
    expect(
      getEvalCreateOnboardingParams(
        "?source=onboarding&step=run&source_type=dataset&source_id=data-1&run_id=run-1",
      ),
    ).toEqual({
      isOnboarding: true,
      runId: "run-1",
      sourceId: "data-1",
      sourceType: "dataset",
      step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
    });
  });

  it("preserves onboarding query params when moving to a draft route", () => {
    expect(
      buildEvalCreateDraftHref(
        "eval-1",
        "?source=onboarding&step=scorer&source_type=dataset&source_id=data-1",
      ),
    ).toBe(
      "/dashboard/evaluations/create/eval-1?source=onboarding&step=scorer&source_type=dataset&source_id=data-1",
    );
  });

  it("returns copy and stage for supported steps", () => {
    expect(
      getEvalCreateOnboardingCopy({
        step: EVAL_CREATE_ONBOARDING_STEPS.DATA,
      }),
    ).toMatchObject({
      currentStep: "Source",
      title: "Create the eval source",
    });
    expect(evalCreateOnboardingStage(EVAL_CREATE_ONBOARDING_STEPS.RUN)).toBe(
      "run_eval",
    );
  });

  it("chooses the initial source tab for onboarding create routes", () => {
    expect(
      getEvalCreateInitialSourceTab({
        isOnboarding: true,
        step: EVAL_CREATE_ONBOARDING_STEPS.DATA,
      }),
    ).toBe(EVAL_CREATE_SOURCE_TABS.DATASET);

    expect(
      getEvalCreateInitialSourceTab({
        isOnboarding: true,
        sourceType: "trace_project",
        step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
      }),
    ).toBe(EVAL_CREATE_SOURCE_TABS.TRACING);

    expect(
      getEvalCreateInitialSourceTab({
        isOnboarding: false,
        step: EVAL_CREATE_ONBOARDING_STEPS.DATA,
      }),
    ).toBe(EVAL_CREATE_SOURCE_TABS.CUSTOM);
  });

  it("builds a safe route focus payload", () => {
    expect(
      buildEvalRouteFocusPayload({
        draftId: "eval-1",
        sourceId: "data-1",
        sourceType: "dataset",
        step: EVAL_CREATE_ONBOARDING_STEPS.SCORER,
      }),
    ).toMatchObject({
      eventName: "onboarding_eval_route_focus_viewed",
      primaryPath: "evals",
      stage: "add_eval_scorer",
      source: "eval_create_onboarding",
      artifactType: "eval_route",
      artifactId: "data-1",
      metadata: {
        draft_id: "eval-1",
        source_id: "data-1",
        source_type: "dataset",
        step: "scorer",
      },
      idempotencyKey: "onboarding_eval_route_focus_viewed:scorer:data-1",
    });
  });

  it("builds a source-selected payload without source content", () => {
    const payload = buildEvalSourceSelectedPayload({
      draftId: "eval-1",
      rowType: "Span",
      sourceId: "project-1",
      sourceType: "trace_project",
      step: EVAL_CREATE_ONBOARDING_STEPS.DATA,
      surface: "tracing",
    });

    expect(payload).toMatchObject({
      eventName: "onboarding_eval_source_selected",
      primaryPath: "evals",
      stage: "create_eval_dataset",
      source: "eval_create_onboarding",
      artifactType: "eval_source",
      artifactId: "project-1",
      metadata: {
        draft_id: "eval-1",
        row_type: "Span",
        source_id: "project-1",
        source_type: "trace_project",
        step: "data",
        surface: "tracing",
      },
      idempotencyKey: "onboarding_eval_source_selected:trace_project:project-1",
    });
    expect(payload.metadata).not.toHaveProperty("rows");
    expect(payload.metadata).not.toHaveProperty("trace");
    expect(payload.metadata).not.toHaveProperty("prompt");
  });

  it("builds a scorer-created payload without source content", () => {
    expect(
      buildEvalScorerCreatedPayload({
        evalId: "eval-1",
        evalType: "agent",
        sourceId: "data-1",
        sourceType: "dataset",
        step: EVAL_CREATE_ONBOARDING_STEPS.SCORER,
      }),
    ).toMatchObject({
      eventName: "eval_scorer_created",
      primaryPath: "evals",
      stage: "add_eval_scorer",
      source: "eval_create_onboarding",
      artifactType: "eval_scorer",
      artifactId: "eval-1",
      metadata: {
        eval_id: "eval-1",
        eval_type: "agent",
        is_composite: false,
        source_id: "data-1",
        source_type: "dataset",
        step: "scorer",
      },
      idempotencyKey: "eval_scorer_created:data-1:eval-1",
    });
  });

  it("builds a run-completed payload without result content", () => {
    const payload = buildEvalRunCompletedPayload({
      evalId: "eval-1",
      evalType: "agent",
      mode: "single",
      result: {
        log_id: "log-1",
        output: "Failed",
        reason: "Do not include result content",
      },
      sourceId: "data-1",
      sourceType: "dataset",
    });

    expect(payload).toMatchObject({
      eventName: "eval_run_completed",
      primaryPath: "evals",
      stage: "run_eval",
      source: "eval_create_onboarding",
      artifactType: "eval_run",
      artifactId: "log-1",
      metadata: {
        eval_id: "eval-1",
        eval_type: "agent",
        is_composite: false,
        log_id: "log-1",
        mode: "single",
        run_id: "log-1",
        source_id: "data-1",
        source_type: "dataset",
        status: "completed",
        step: "run",
      },
      idempotencyKey: "eval_run_completed:data-1:eval-1:log-1",
    });
    expect(payload.metadata).not.toHaveProperty("output");
    expect(payload.metadata).not.toHaveProperty("reason");
  });

  it("parses eval review onboarding query params", () => {
    expect(
      getEvalReviewOnboardingParams(
        "?tab=usage&source=onboarding&step=review&run_id=run-1",
      ),
    ).toEqual({
      isOnboarding: true,
      runId: "run-1",
      step: "review",
      tab: "usage",
    });
  });

  it("returns review copy for the review route focus panel", () => {
    expect(getEvalReviewOnboardingCopy()).toMatchObject({
      currentStep: "Review",
      title: "Review the eval result",
      steps: [
        { label: "Source", complete: true },
        { label: "Scorer", complete: true },
        { label: "Run", complete: true },
        { label: "Review", complete: false },
      ],
    });
  });

  it("preserves review onboarding params when moving from usage list to detail", () => {
    expect(
      buildEvalReviewDetailHref(
        "eval-1",
        "?tab=usage&source=onboarding&step=review&run_id=run-1",
      ),
    ).toBe(
      "/dashboard/evaluations/eval-1?tab=usage&source=onboarding&step=review&run_id=run-1",
    );

    expect(buildEvalReviewDetailHref("eval-1", "?tab=usage")).toBe(
      "/dashboard/evaluations/eval-1",
    );
  });

  it("builds a review route focus payload", () => {
    expect(
      buildEvalReviewRouteFocusPayload({
        evalId: "eval-1",
        route: "eval_detail",
        runId: "run-1",
      }),
    ).toMatchObject({
      eventName: "onboarding_eval_route_focus_viewed",
      primaryPath: "evals",
      stage: "review_eval_failures",
      source: "eval_review_onboarding",
      artifactType: "eval_review_route",
      artifactId: "run-1",
      metadata: {
        eval_id: "eval-1",
        route: "eval_detail",
        run_id: "run-1",
        step: "review",
        tab: "usage",
      },
      idempotencyKey: "onboarding_eval_route_focus_viewed:review:run-1",
    });
  });

  it("builds an eval failures reviewed payload without captured content", () => {
    expect(
      buildEvalFailuresReviewedPayload({
        evalId: "eval-1",
        runId: "run-1",
      }),
    ).toMatchObject({
      eventName: "eval_failures_reviewed",
      primaryPath: "evals",
      stage: "review_eval_failures",
      source: "eval_review_onboarding",
      artifactType: "eval_run",
      artifactId: "run-1",
      metadata: {
        eval_id: "eval-1",
        run_id: "run-1",
        step: "review",
        tab: "usage",
      },
      idempotencyKey: "eval_failures_reviewed:run-1:eval-1",
    });
  });

  it("parses eval failure action onboarding query params", () => {
    expect(
      getEvalFailureActionOnboardingParams(
        "?source=onboarding&step=fix-eval-failure&run_id=run-1",
      ),
    ).toEqual({
      isOnboarding: true,
      runId: "run-1",
      step: "fix-eval-failure",
    });

    expect(getEvalFailureActionOnboardingParams("?source=onboarding")).toEqual({
      isOnboarding: false,
      runId: null,
      step: null,
    });
  });

  it("builds an eval failure action payload without feedback content", () => {
    const payload = buildEvalFailureActionCreatedPayload({
      actionType: "recalculate",
      evalId: "eval-1",
      evalLogId: "log-1",
      feedbackId: "feedback-1",
      rowSource: "eval_playground",
      runId: "run-1",
      step: "review",
    });

    expect(payload).toMatchObject({
      eventName: "eval_failure_action_created",
      primaryPath: "evals",
      stage: "fix_eval_source",
      source: "eval_review_onboarding",
      artifactType: "eval_feedback",
      artifactId: "feedback-1",
      metadata: {
        action_type: "recalculate",
        eval_id: "eval-1",
        eval_log_id: "log-1",
        feedback_id: "feedback-1",
        row_source: "eval_playground",
        run_id: "run-1",
        step: "review",
      },
      idempotencyKey: "eval_failure_action_created:feedback-1:eval-1",
    });
    expect(payload.metadata).not.toHaveProperty("value");
    expect(payload.metadata).not.toHaveProperty("explanation");
    expect(payload.metadata).not.toHaveProperty("reason");
  });
});
