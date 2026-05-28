import { describe, expect, it } from "vitest";
import {
  buildEvalCreateDraftHref,
  buildEvalDatasetCreatedPayload,
  buildEvalFailureActionCreatedPayload,
  buildEvalFailuresReviewedPayload,
  buildEvalReviewDetailHref,
  buildEvalReviewRouteFocusPayload,
  buildEvalReviewStepHref,
  buildEvalRouteFocusPayload,
  buildEvalRunStepHref,
  buildEvalRunCompletedPayload,
  buildEvalScorerCreatedPayload,
  buildEvalScorerSourceHref,
  buildEvalSourceSelectedPayload,
  buildEvalSourceSetupHref,
  EVAL_CREATE_ONBOARDING_STEPS,
  EVAL_CREATE_SOURCE_TABS,
  evalCreateOnboardingStage,
  evalUsageLogMatchesRun,
  getEvalUsageLogId,
  getEvalUsageReviewOutcome,
  getEvalCreateInitialSourceTab,
  getEvalCreateOnboardingCopy,
  getEvalCreateOnboardingParams,
  getEvalOnboardingSourceSummary,
  getEvalFailureActionOnboardingParams,
  getEvalReviewOnboardingCopy,
  getEvalReviewOnboardingParams,
  getEvalRunResultId,
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

  it("builds eval source setup and scorer continuation hrefs", () => {
    expect(buildEvalSourceSetupHref()).toBe(
      "/dashboard/develop?source=onboarding&action=create-eval-dataset",
    );
    expect(buildEvalScorerSourceHref({ sourceId: "data-1" })).toBe(
      "/dashboard/evaluations/create?source=onboarding&step=scorer&source_type=dataset&source_id=data-1",
    );
    expect(
      buildEvalRunStepHref({
        evalId: "eval-1",
        sourceId: "data-1",
        sourceType: "dataset",
      }),
    ).toBe(
      "/dashboard/evaluations/create/eval-1?source=onboarding&step=run&source_type=dataset&source_id=data-1",
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

  it("summarizes a completed onboarding eval source without source content", () => {
    expect(
      getEvalOnboardingSourceSummary({
        isOnboarding: true,
        sourceId: "data-1",
        sourceType: "dataset",
        step: EVAL_CREATE_ONBOARDING_STEPS.SCORER,
      }),
    ).toEqual({
      description: "The next scorer you save will evaluate this source.",
      label: "Dataset ready",
    });

    expect(
      getEvalOnboardingSourceSummary({
        isOnboarding: true,
        sourceId: "data-1",
        sourceType: "dataset",
        step: EVAL_CREATE_ONBOARDING_STEPS.DATA,
      }),
    ).toBeNull();
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

  it("builds an eval dataset-created payload without dataset content", () => {
    const payload = buildEvalDatasetCreatedPayload({
      datasetId: "data-1",
      sourceMethod: "manual",
    });

    expect(payload).toMatchObject({
      eventName: "eval_dataset_created",
      primaryPath: "evals",
      stage: "create_eval_dataset",
      source: "eval_create_onboarding",
      artifactType: "eval_source",
      artifactId: "data-1",
      metadata: {
        dataset_id: "data-1",
        source_id: "data-1",
        source_method: "manual",
        source_type: "dataset",
        step: "data",
      },
      idempotencyKey: "eval_dataset_created:dataset:data-1",
    });
    expect(payload.metadata).not.toHaveProperty("rows");
    expect(payload.metadata).not.toHaveProperty("columns");
    expect(payload.metadata).not.toHaveProperty("name");
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
    expect(buildEvalReviewStepHref({ evalId: "eval-1", runId: "run-1" })).toBe(
      "/dashboard/evaluations/eval-1?tab=usage&source=onboarding&step=review&run_id=run-1",
    );
    expect(buildEvalReviewStepHref({ runId: "run-1" })).toBe(
      "/dashboard/evaluations/usage?tab=usage&source=onboarding&step=review&run_id=run-1",
    );

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

  it("extracts the safest available run id from eval run results", () => {
    expect(getEvalRunResultId({ log_id: "log-1" })).toBe("log-1");
    expect(
      getEvalRunResultId({
        eval_task_id: "task-1",
        log_id: "log-1",
      }),
    ).toBe("task-1");
    expect(getEvalRunResultId({})).toBeNull();
  });

  it("matches review logs to run ids without reading result content", () => {
    expect(
      evalUsageLogMatchesRun(
        {
          id: "log-1",
          output: "Do not inspect this field",
          reason: "Do not inspect this field",
        },
        "log-1",
      ),
    ).toBe(true);
    expect(
      evalUsageLogMatchesRun(
        {
          detail: {
            eval_task_id: "task-1",
          },
        },
        "task-1",
      ),
    ).toBe(true);
    expect(evalUsageLogMatchesRun({ id: "log-2" }, "log-1")).toBe(false);
  });

  it("extracts eval usage log ids without reading result content", () => {
    expect(
      getEvalUsageLogId({
        detail: { eval_log_id: "detail-log-1" },
        output: "Do not inspect this field",
        reason: "Do not inspect this field",
      }),
    ).toBe("detail-log-1");
    expect(getEvalUsageLogId({ evaluation_id: "evaluation-1" })).toBe(
      "evaluation-1",
    );
  });

  it("classifies eval usage review outcome from status fields", () => {
    expect(getEvalUsageReviewOutcome({ result: "Failed" })).toBe(
      "failure_reviewed",
    );
    expect(getEvalUsageReviewOutcome({ score: 0.4 })).toBe(
      "weak_result_reviewed",
    );
    expect(getEvalUsageReviewOutcome({ result: "Passed", score: 0.95 })).toBe(
      "result_summary_reviewed",
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
        evalLogId: "log-1",
        reviewOutcome: "failure_reviewed",
        rowSource: "eval_playground",
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
        eval_log_id: "log-1",
        review_outcome: "failure_reviewed",
        review_surface: "usage_log_detail",
        row_source: "eval_playground",
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
