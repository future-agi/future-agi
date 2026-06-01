import { describe, expect, it } from "vitest";
import {
  appendEvalOnboardingAttributionToHref,
  buildEvalCreateDraftHref,
  buildEvalDatasetCreatedPayload,
  buildEvalFixRerunCompletedPayload,
  buildEvalFixRerunReviewedPayload,
  buildEvalFirstQualityLoopCompletedPayload,
  buildEvalFailureActionCreatedPayload,
  buildEvalFailuresReviewedPayload,
  buildEvalPostRepairHomeHref,
  buildEvalReviewDetailHref,
  buildEvalReviewRouteFocusPayload,
  buildEvalReviewStepHref,
  buildEvalRouteFocusPayload,
  buildEvalRunCompletedPayload,
  buildEvalRunClickedPayload,
  buildEvalRunStepHref,
  buildEvalScorerEditCtaClickedPayload,
  buildEvalScorerEditHref,
  buildEvalScorerCreatedPayload,
  buildEvalScorerSourceHref,
  buildEvalSourceFixCtaClickedPayload,
  buildEvalSourceFixHref,
  buildEvalSourceFixRerunClickedPayload,
  buildEvalSourceFixRouteFocusPayload,
  buildEvalSourceSelectedPayload,
  buildEvalSourceSetupHref,
  EVAL_CREATE_ONBOARDING_STEPS,
  EVAL_CREATE_SOURCE_TABS,
  EVAL_FIX_RERUN_ORIGINS,
  EVAL_REVIEW_ACTIONS,
  evalSetupQuickStartAttributionFromSearch,
  evalCreateOnboardingStage,
  evalUsageLogMatchesRun,
  getEvalReviewActionKind,
  getEvalUsageLogId,
  getEvalUsageReviewOutcome,
  getEvalCreateInitialSourceTab,
  getEvalCreateOnboardingCopy,
  getEvalCreateOnboardingParams,
  getEvalDetailTabFromSearch,
  getEvalOnboardingSourceSummary,
  getEvalFailureActionOnboardingParams,
  getEvalReviewOnboardingCopy,
  getEvalReviewOnboardingParams,
  getEvalRunFailureCount,
  getEvalRunResultId,
  getEvalStarterScorer,
  getEvalSourceFixOnboardingCopy,
  getEvalSourceFixOnboardingParams,
  shouldAutoConfirmEvalOnboardingSource,
  shouldAutoSaveEvalOnboardingStarterScorer,
} from "./evalCreateOnboarding";

const EVAL_QUICK_START_SEARCH =
  "?quick_start_goal=evaluate_quality&quick_start_id=evals&quick_start_primary_path=evals";

describe("evalCreateOnboarding", () => {
  it("parses eval create onboarding query params", () => {
    expect(
      getEvalCreateOnboardingParams(
        "?source=onboarding&step=run&source_type=dataset&source_id=data-1&run_id=run-1&provider=Anthropic&language=TypeScript",
      ),
    ).toEqual({
      isOnboarding: true,
      previousRunId: null,
      rerunFrom: null,
      runId: "run-1",
      setupLanguage: "typescript",
      setupProvider: "anthropic",
      sourceId: "data-1",
      sourceType: "dataset",
      step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
      traceId: null,
      tourAnchor: null,
    });
  });

  it("parses eval create journey-step params from Home CTAs", () => {
    expect(
      getEvalCreateOnboardingParams(
        "?tour_anchor=eval_source_button&journey_step=create_eval_dataset",
      ),
    ).toMatchObject({
      isOnboarding: true,
      step: EVAL_CREATE_ONBOARDING_STEPS.DATA,
      tourAnchor: "eval_source_button",
    });

    expect(
      getEvalCreateOnboardingParams(
        "?tour_anchor=eval_run_button&journey_step=run_eval",
      ),
    ).toMatchObject({
      isOnboarding: true,
      step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
      tourAnchor: "eval_run_button",
    });

    expect(
      getEvalCreateOnboardingParams(
        "?tour_anchor=eval_scorer_button&journey_step=add_eval_scorer",
      ),
    ).toMatchObject({
      isOnboarding: true,
      step: EVAL_CREATE_ONBOARDING_STEPS.SCORER,
      tourAnchor: "eval_scorer_button",
    });
  });

  it("parses fix-rerun context on eval create routes", () => {
    expect(
      getEvalCreateOnboardingParams(
        "?source=onboarding&step=run&source_type=dataset&source_id=data-1&rerun_from=source_fix&previous_run_id=run-1",
      ),
    ).toMatchObject({
      isOnboarding: true,
      previousRunId: "run-1",
      rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
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
      buildEvalScorerSourceHref({
        evalId: "eval-1",
        setupLanguage: "typescript",
        setupProvider: "anthropic",
        sourceId: "project-1",
        sourceType: "trace_project",
      }),
    ).toBe(
      "/dashboard/evaluations/create/eval-1?source=onboarding&step=scorer&source_type=trace_project&source_id=project-1&provider=anthropic&language=typescript",
    );
    expect(
      buildEvalScorerEditHref({
        evalId: "eval-1",
        previousRunId: "run-1",
        rerunFrom: EVAL_FIX_RERUN_ORIGINS.SCORER_EDIT,
        sourceId: "data-1",
        sourceType: "dataset",
      }),
    ).toBe(
      "/dashboard/evaluations/create/eval-1?source=onboarding&step=scorer&source_type=dataset&source_id=data-1&rerun_from=scorer_edit&previous_run_id=run-1",
    );
    expect(buildEvalScorerEditHref()).toBeNull();
    expect(
      buildEvalRunStepHref({
        evalId: "eval-1",
        search: "?provider=llama_index&language=python",
        sourceId: "data-1",
        sourceType: "dataset",
      }),
    ).toBe(
      "/dashboard/evaluations/create/eval-1?source=onboarding&step=run&source_type=dataset&source_id=data-1&provider=llamaindex&language=python",
    );
    expect(
      buildEvalRunStepHref({
        evalId: "eval-1",
        previousRunId: "run-1",
        rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
        setupIntent: {
          setup_language: "typescript",
          setup_provider: "openai-agents",
        },
        sourceId: "data-1",
        sourceType: "dataset",
      }),
    ).toBe(
      "/dashboard/evaluations/create/eval-1?source=onboarding&step=run&source_type=dataset&source_id=data-1&rerun_from=source_fix&previous_run_id=run-1&provider=openai_agents&language=typescript",
    );
  });

  it("keeps setup quick-start attribution across eval create routes", () => {
    const attribution = evalSetupQuickStartAttributionFromSearch(
      EVAL_QUICK_START_SEARCH,
    );

    expect(attribution).toEqual({
      quick_start_goal: "evaluate_quality",
      quick_start_id: "evals",
      quick_start_primary_path: "evals",
    });
    expect(
      appendEvalOnboardingAttributionToHref(
        "/dashboard/evaluations/create?source=onboarding",
        attribution,
      ),
    ).toBe(
      "/dashboard/evaluations/create?source=onboarding&quick_start_goal=evaluate_quality&quick_start_id=evals&quick_start_primary_path=evals",
    );
    expect(
      buildEvalSourceSetupHref({
        search: EVAL_QUICK_START_SEARCH,
      }),
    ).toBe(
      "/dashboard/develop?source=onboarding&action=create-eval-dataset&quick_start_goal=evaluate_quality&quick_start_id=evals&quick_start_primary_path=evals",
    );
    expect(
      buildEvalScorerSourceHref({
        quickStartAttribution: attribution,
        sourceId: "data-1",
      }),
    ).toBe(
      "/dashboard/evaluations/create?source=onboarding&step=scorer&source_type=dataset&source_id=data-1&quick_start_goal=evaluate_quality&quick_start_id=evals&quick_start_primary_path=evals",
    );
    expect(
      buildEvalRunStepHref({
        evalId: "eval-1",
        quickStartAttribution: attribution,
        sourceId: "data-1",
        sourceType: "dataset",
      }),
    ).toBe(
      "/dashboard/evaluations/create/eval-1?source=onboarding&step=run&source_type=dataset&source_id=data-1&quick_start_goal=evaluate_quality&quick_start_id=evals&quick_start_primary_path=evals",
    );
  });

  it("preserves trace context across trace-project evaluator routes and payloads", () => {
    const traceContext = {
      sourceId: "project-1",
      sourceType: "trace_project",
      traceId: "trace-1",
    };
    const searchParamsFor = (href) =>
      new URL(href, "http://localhost").searchParams;

    expect(
      getEvalCreateOnboardingParams(
        "?source=onboarding&step=scorer&source_type=trace_project&source_id=project-1&trace_id=trace-1",
      ),
    ).toMatchObject({ traceId: "trace-1" });
    expect(
      getEvalReviewOnboardingParams(
        "?tab=usage&source=onboarding&step=review&run_id=run-1&source_type=trace_project&source_id=project-1&trace_id=trace-1",
      ),
    ).toMatchObject({ traceId: "trace-1" });
    expect(
      getEvalFailureActionOnboardingParams(
        "?source=onboarding&step=review&run_id=run-1&source_type=trace_project&source_id=project-1&trace_id=trace-1",
      ),
    ).toMatchObject({ traceId: "trace-1" });
    expect(
      getEvalSourceFixOnboardingParams(
        "?source=onboarding&step=fix-eval-failure&source_type=trace_project&source_id=project-1&trace_id=trace-1",
      ),
    ).toMatchObject({ traceId: "trace-1" });

    [
      buildEvalScorerSourceHref({ evalId: "eval-1", ...traceContext }),
      buildEvalRunStepHref({ evalId: "eval-1", ...traceContext }),
      buildEvalReviewStepHref({
        evalId: "eval-1",
        runId: "run-1",
        ...traceContext,
      }),
      buildEvalSourceFixHref({
        evalId: "eval-1",
        runId: "run-1",
        ...traceContext,
      }),
      buildEvalPostRepairHomeHref({ runId: "run-1", ...traceContext }),
      buildEvalReviewDetailHref(
        "eval-1",
        "?tab=usage&source=onboarding&step=review&run_id=run-1&source_type=trace_project&source_id=project-1&trace_id=trace-1",
      ),
    ].forEach((href) => {
      expect(searchParamsFor(href).get("trace_id")).toBe("trace-1");
    });

    expect(
      buildEvalRouteFocusPayload({
        step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
        ...traceContext,
      }).metadata,
    ).toMatchObject({ trace_id: "trace-1" });
    expect(
      buildEvalSourceSelectedPayload({
        step: EVAL_CREATE_ONBOARDING_STEPS.DATA,
        ...traceContext,
      }).metadata,
    ).toMatchObject({ trace_id: "trace-1" });
    expect(
      buildEvalScorerCreatedPayload({
        evalId: "eval-1",
        step: EVAL_CREATE_ONBOARDING_STEPS.SCORER,
        ...traceContext,
      }).metadata,
    ).toMatchObject({ trace_id: "trace-1" });
    expect(
      buildEvalRunCompletedPayload({
        evalId: "eval-1",
        result: { log_id: "run-1" },
        ...traceContext,
      }).metadata,
    ).toMatchObject({ trace_id: "trace-1" });
    expect(
      buildEvalFirstQualityLoopCompletedPayload({
        evalId: "eval-1",
        runId: "run-1",
        ...traceContext,
      }).metadata,
    ).toMatchObject({ trace_id: "trace-1" });
  });

  it("returns copy and stage for supported steps", () => {
    expect(
      getEvalCreateOnboardingCopy({
        step: EVAL_CREATE_ONBOARDING_STEPS.DATA,
      }),
    ).toMatchObject({
      currentStep: "Source",
      title: "Choose what to test",
    });
    expect(evalCreateOnboardingStage(EVAL_CREATE_ONBOARDING_STEPS.RUN)).toBe(
      "run_eval",
    );
    expect(
      getEvalCreateOnboardingCopy({
        setupLanguage: "python",
        setupProvider: "anthropic",
        sourceType: "trace_project",
        step: EVAL_CREATE_ONBOARDING_STEPS.SCORER,
      }),
    ).toMatchObject({
      currentStep: "First quality check",
      description:
        "A starter quality check is loaded for Anthropic Python traces. Create it, then run it once.",
      title: "Create Anthropic Python quality check",
      steps: [
        { label: "Trace source", complete: true },
        { label: "Quality check", complete: false },
        { label: "Run", complete: false },
      ],
    });
    expect(
      getEvalCreateOnboardingCopy({
        setupLanguage: "typescript",
        setupProvider: "openai",
        sourceType: "trace_project",
        step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
      }),
    ).toMatchObject({
      currentStep: "Run quality check",
      description:
        "Run the saved quality check on OpenAI TypeScript traces so the first result is reviewable.",
      title: "Run OpenAI TypeScript quality check",
    });
  });

  it("returns rerun copy when the eval follows a source fix", () => {
    expect(
      getEvalCreateOnboardingCopy({
        rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
        step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
      }),
    ).toMatchObject({
      currentStep: "Rerun",
      title: "Rerun the eval",
      steps: [
        { label: "Review", complete: true },
        { label: "Fix", complete: true },
        { label: "Rerun", complete: false },
        { label: "Inspect", complete: false },
      ],
    });
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
        step: EVAL_CREATE_ONBOARDING_STEPS.SCORER,
      }),
    ).toBe(EVAL_CREATE_SOURCE_TABS.TRACING);

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

  it("auto-confirms only trace-project sources on the onboarding data step", () => {
    expect(
      shouldAutoConfirmEvalOnboardingSource({
        isOnboarding: true,
        sourceId: "project-1",
        sourceType: "trace_project",
        step: EVAL_CREATE_ONBOARDING_STEPS.DATA,
      }),
    ).toBe(true);

    expect(
      shouldAutoConfirmEvalOnboardingSource({
        isOnboarding: true,
        sourceId: "dataset-1",
        sourceType: "dataset",
        step: EVAL_CREATE_ONBOARDING_STEPS.DATA,
      }),
    ).toBe(false);

    expect(
      shouldAutoConfirmEvalOnboardingSource({
        isOnboarding: true,
        sourceId: "project-1",
        sourceType: "trace_project",
        step: EVAL_CREATE_ONBOARDING_STEPS.SCORER,
      }),
    ).toBe(false);
  });

  it("auto-saves only trace-project starter scorers on the onboarding scorer step", () => {
    expect(
      shouldAutoSaveEvalOnboardingStarterScorer({
        isOnboarding: true,
        sourceId: "project-1",
        sourceType: "trace_project",
        step: EVAL_CREATE_ONBOARDING_STEPS.SCORER,
      }),
    ).toBe(true);

    expect(
      shouldAutoSaveEvalOnboardingStarterScorer({
        isOnboarding: true,
        sourceId: "dataset-1",
        sourceType: "dataset",
        step: EVAL_CREATE_ONBOARDING_STEPS.SCORER,
      }),
    ).toBe(false);

    expect(
      shouldAutoSaveEvalOnboardingStarterScorer({
        isOnboarding: true,
        sourceId: "project-1",
        sourceType: "trace_project",
        step: EVAL_CREATE_ONBOARDING_STEPS.DATA,
      }),
    ).toBe(false);
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
      description:
        "Starter scorer is ready. Edit it or save to run this source.",
      label: "Dataset ready",
    });

    expect(
      getEvalOnboardingSourceSummary({
        isOnboarding: true,
        sourceId: "data-1",
        sourceType: "dataset",
        step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
      }),
    ).toEqual({
      description: "Run the saved scorer on this source.",
      label: "Dataset ready",
    });

    expect(
      getEvalOnboardingSourceSummary({
        isOnboarding: true,
        sourceId: "data-1",
        sourceType: "dataset",
        step: EVAL_CREATE_ONBOARDING_STEPS.DATA,
      }),
    ).toEqual({
      description: "Use this source to add a scorer next.",
      label: "Dataset selected",
    });
  });

  it("keeps observe package context in trace-project eval source summaries", () => {
    expect(
      getEvalOnboardingSourceSummary({
        isOnboarding: true,
        setupLanguage: "python",
        setupProvider: "anthropic",
        sourceId: "project-1",
        sourceType: "trace_project",
        step: EVAL_CREATE_ONBOARDING_STEPS.DATA,
      }),
    ).toEqual({
      description:
        "Source is locked to this Anthropic Python trace project. Add a scorer next.",
      label: "Anthropic Python trace project locked",
    });

    expect(
      getEvalOnboardingSourceSummary({
        isOnboarding: true,
        setupLanguage: "typescript",
        setupProvider: "openai",
        sourceId: "project-1",
        sourceType: "trace_project",
        step: EVAL_CREATE_ONBOARDING_STEPS.SCORER,
      }),
    ).toEqual({
      description:
        "Starter scorer is loaded for OpenAI TypeScript traces. Create the quality check, then run it once.",
      label: "OpenAI TypeScript trace project locked",
    });

    expect(
      getEvalOnboardingSourceSummary({
        isOnboarding: true,
        setupLanguage: "python",
        setupProvider: "llama_index",
        sourceId: "project-1",
        sourceType: "trace_project",
        step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
      }),
    ).toEqual({
      description: "Run the saved quality check on LlamaIndex Python traces.",
      label: "LlamaIndex Python trace project ready",
    });

    expect(
      getEvalOnboardingSourceSummary({
        isOnboarding: true,
        sourceId: "project-1",
        sourceType: "trace_project",
        step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
      }),
    ).toEqual({
      description: "Run the saved quality check on this trace project.",
      label: "Trace project ready",
    });
  });

  it("builds an OSS-safe starter scorer for the scorer step", () => {
    const starter = getEvalStarterScorer({
      sourceId: "project-1",
      sourceType: "trace_project",
    });

    expect(starter).toMatchObject({
      codeLanguage: "python",
      description: "Starter scorer for trace project.",
      evalType: "code",
      name: "output-quality-project-1",
      outputType: "percentage",
      passThreshold: 0.7,
    });
    expect(starter.code).toContain(
      "def evaluate(output: Any = None, context: dict = None, **kwargs):",
    );
    expect(starter.code).toContain('context.get("span")');
    expect(starter.code).toContain('kwargs.get("span_context")');
  });

  it("builds a safe route focus payload", () => {
    expect(
      buildEvalRouteFocusPayload({
        draftId: "eval-1",
        previousRunId: "run-0",
        rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
        runId: "run-1",
        sourceId: "data-1",
        sourceType: "dataset",
        step: EVAL_CREATE_ONBOARDING_STEPS.RUN,
      }),
    ).toMatchObject({
      eventName: "onboarding_eval_route_focus_viewed",
      primaryPath: "evals",
      stage: "run_eval",
      source: "eval_create_onboarding",
      artifactType: "eval",
      artifactId: "data-1",
      metadata: {
        draft_id: "eval-1",
        previous_run_id: "run-0",
        rerun_from: "source_fix",
        run_id: "run-1",
        source_id: "data-1",
        source_type: "dataset",
        step: "run",
      },
      idempotencyKey: "onboarding_eval_route_focus_viewed:run:data-1",
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
      artifactType: "observe_project",
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
      artifactType: "dataset",
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

  it("adds setup quick-start attribution to eval activation payloads", () => {
    const attribution = evalSetupQuickStartAttributionFromSearch(
      EVAL_QUICK_START_SEARCH,
    );

    expect(
      buildEvalDatasetCreatedPayload({
        datasetId: "data-1",
        quickStartAttribution: attribution,
        sourceMethod: "manual",
      }),
    ).toMatchObject({
      eventName: "eval_dataset_created",
      quick_start_goal: "evaluate_quality",
      quick_start_id: "evals",
      quick_start_primary_path: "evals",
    });

    expect(
      buildEvalFirstQualityLoopCompletedPayload({
        evalId: "eval-1",
        previousRunId: "run-1",
        quickStartAttribution: attribution,
        rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
        runId: "run-2",
      }),
    ).toMatchObject({
      eventName: "first_quality_loop_completed",
      quick_start_goal: "evaluate_quality",
      quick_start_id: "evals",
      quick_start_primary_path: "evals",
    });
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
        failed_spans_count: 2,
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
        failure_count: 2,
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

  it("extracts failure count from eval run result variants", () => {
    expect(getEvalRunFailureCount({ failure_count: "3" })).toBe(3);
    expect(getEvalRunFailureCount({ failed_spans: ["a", "b"] })).toBe(2);
    expect(getEvalRunFailureCount({ status: "failed" })).toBe(1);
    expect(getEvalRunFailureCount({ status: "completed" })).toBeNull();
  });

  it("builds a run-clicked payload before result content exists", () => {
    const payload = buildEvalRunClickedPayload({
      evalId: "eval-1",
      evalType: "code",
      mode: "single",
      sourceId: "project-1",
      sourceType: "trace_project",
    });

    expect(payload).toMatchObject({
      eventName: "onboarding_eval_run_clicked",
      primaryPath: "evals",
      stage: "run_eval",
      source: "eval_create_onboarding",
      artifactType: "eval",
      artifactId: "eval-1",
      metadata: {
        eval_id: "eval-1",
        eval_type: "code",
        is_composite: false,
        mode: "single",
        source_id: "project-1",
        source_type: "trace_project",
        step: "run",
      },
      idempotencyKey: "onboarding_eval_run_clicked:project-1:eval-1",
    });
    expect(payload.metadata).not.toHaveProperty("run_id");
    expect(payload.metadata).not.toHaveProperty("result");
  });

  it("builds a fix-rerun completed payload without result content", () => {
    const payload = buildEvalFixRerunCompletedPayload({
      evalId: "eval-1",
      evalType: "agent",
      mode: "single",
      previousRunId: "run-1",
      rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
      result: {
        failed_count: 1,
        log_id: "run-2",
        output: "Do not include result content",
        reason: "Do not include result content",
      },
      sourceId: "data-1",
      sourceType: "dataset",
    });

    expect(payload).toMatchObject({
      eventName: "onboarding_eval_fix_rerun_completed",
      primaryPath: "evals",
      stage: "eval_next_loop",
      source: "eval_review_onboarding",
      artifactType: "eval_run",
      artifactId: "run-2",
      metadata: {
        eval_id: "eval-1",
        eval_type: "agent",
        failure_count: 1,
        is_composite: false,
        log_id: "run-2",
        mode: "single",
        previous_run_id: "run-1",
        rerun_from: "source_fix",
        run_id: "run-2",
        source_id: "data-1",
        source_type: "dataset",
        status: "completed",
        step: "run",
      },
      idempotencyKey:
        "onboarding_eval_fix_rerun_completed:source_fix:run-1:run-2",
    });
    expect(payload.metadata).not.toHaveProperty("output");
    expect(payload.metadata).not.toHaveProperty("reason");
  });

  it("parses eval review onboarding query params", () => {
    expect(
      getEvalReviewOnboardingParams(
        "?tab=usage&source=onboarding&step=review&run_id=run-1&source_type=dataset&source_id=data-1",
      ),
    ).toEqual({
      isOnboarding: true,
      previousRunId: null,
      rerunFrom: null,
      runId: "run-1",
      setupLanguage: null,
      setupProvider: null,
      sourceId: "data-1",
      sourceType: "dataset",
      step: "review",
      tab: "usage",
      traceId: null,
      tourAnchor: null,
    });

    expect(
      getEvalReviewOnboardingParams(
        "?tab=usage&source=onboarding&step=review&run_id=run-2&rerun_from=source_fix&previous_run_id=run-1",
      ),
    ).toMatchObject({
      isOnboarding: true,
      previousRunId: "run-1",
      rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
      runId: "run-2",
      step: "review",
      tab: "usage",
    });
  });

  it("resolves the eval detail tab from the route query", () => {
    expect(getEvalDetailTabFromSearch("?tab=usage")).toBe("usage");
    expect(getEvalDetailTabFromSearch("?tab=feedback")).toBe("feedback");
    expect(getEvalDetailTabFromSearch("?tab=ground_truth")).toBe(
      "ground_truth",
    );
    expect(getEvalDetailTabFromSearch("?tab=unknown")).toBe("details");
    expect(getEvalDetailTabFromSearch("")).toBe("details");
  });

  it("parses eval review journey-step params from Home CTAs", () => {
    expect(
      getEvalReviewOnboardingParams(
        "?tour_anchor=eval_review_button&journey_step=review_eval_failures",
      ),
    ).toMatchObject({
      isOnboarding: true,
      step: "review",
      tab: "usage",
      tourAnchor: "eval_review_button",
    });
  });

  it("returns review copy for the review route focus panel", () => {
    expect(getEvalReviewOnboardingCopy()).toMatchObject({
      currentStep: "Review",
      title: "Review the first quality result",
      steps: [
        { label: "Source", complete: true },
        { label: "Quality check", complete: true },
        { label: "Run", complete: true },
        { label: "Review", complete: false },
        { label: "Fix or finish", complete: false },
      ],
    });

    expect(
      getEvalReviewOnboardingCopy({
        rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
      }),
    ).toMatchObject({
      currentStep: "Review rerun",
      sourceSummary: {
        label: "Repair rerun complete",
      },
      title: "Review the repair attempt",
    });

    expect(
      getEvalReviewOnboardingCopy({
        setupLanguage: "python",
        setupProvider: "anthropic",
        sourceType: "trace_project",
      }),
    ).toMatchObject({
      currentStep: "Review result",
      sourceSummary: {
        label: "Anthropic Python trace quality check run",
      },
      title: "Review trace quality-check result",
      steps: [
        { label: "Trace source", complete: true },
        { label: "Quality check", complete: true },
        { label: "Run", complete: true },
        { label: "Review", complete: false },
      ],
    });
  });

  it("preserves review onboarding params when moving from usage list to detail", () => {
    expect(
      buildEvalReviewStepHref({
        evalId: "eval-1",
        runId: "run-1",
        sourceId: "data-1",
        sourceType: "dataset",
      }),
    ).toBe(
      "/dashboard/evaluations/eval-1?tab=usage&source=onboarding&step=review&run_id=run-1&source_type=dataset&source_id=data-1",
    );
    expect(buildEvalReviewStepHref({ runId: "run-1" })).toBe(
      "/dashboard/evaluations/usage?tab=usage&source=onboarding&step=review&run_id=run-1",
    );
    expect(
      buildEvalReviewStepHref({
        evalId: "eval-1",
        previousRunId: "run-1",
        rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
        runId: "run-2",
        sourceId: "data-1",
        sourceType: "dataset",
      }),
    ).toBe(
      "/dashboard/evaluations/eval-1?tab=usage&source=onboarding&step=review&run_id=run-2&source_type=dataset&source_id=data-1&rerun_from=source_fix&previous_run_id=run-1",
    );

    expect(
      buildEvalReviewDetailHref(
        "eval-1",
        "?tab=usage&source=onboarding&step=review&run_id=run-1&source_type=dataset&source_id=data-1",
      ),
    ).toBe(
      "/dashboard/evaluations/eval-1?tab=usage&source=onboarding&step=review&run_id=run-1&source_type=dataset&source_id=data-1",
    );

    expect(
      buildEvalReviewDetailHref(
        "eval-1",
        "?tab=usage&source=onboarding&step=review&run_id=run-2&source_type=dataset&source_id=data-1&rerun_from=source_fix&previous_run_id=run-1",
      ),
    ).toBe(
      "/dashboard/evaluations/eval-1?tab=usage&source=onboarding&step=review&run_id=run-2&source_type=dataset&source_id=data-1&rerun_from=source_fix&previous_run_id=run-1",
    );

    expect(buildEvalReviewDetailHref("eval-1", "?tab=usage")).toBe(
      "/dashboard/evaluations/eval-1",
    );
  });

  it("keeps setup quick-start attribution across eval review and repair routes", () => {
    const attribution = evalSetupQuickStartAttributionFromSearch(
      EVAL_QUICK_START_SEARCH,
    );

    expect(
      buildEvalReviewStepHref({
        evalId: "eval-1",
        quickStartAttribution: attribution,
        runId: "run-1",
        sourceId: "data-1",
        sourceType: "dataset",
      }),
    ).toBe(
      "/dashboard/evaluations/eval-1?tab=usage&source=onboarding&step=review&run_id=run-1&source_type=dataset&source_id=data-1&quick_start_goal=evaluate_quality&quick_start_id=evals&quick_start_primary_path=evals",
    );
    expect(
      buildEvalReviewDetailHref(
        "eval-1",
        `?tab=usage&source=onboarding&step=review&run_id=run-1&source_type=dataset&source_id=data-1&${EVAL_QUICK_START_SEARCH.slice(
          1,
        )}`,
      ),
    ).toBe(
      "/dashboard/evaluations/eval-1?tab=usage&source=onboarding&step=review&run_id=run-1&source_type=dataset&source_id=data-1&quick_start_goal=evaluate_quality&quick_start_id=evals&quick_start_primary_path=evals",
    );
    expect(
      buildEvalSourceFixHref({
        evalId: "eval-1",
        quickStartAttribution: attribution,
        runId: "run-1",
        sourceId: "data-1",
        sourceType: "dataset",
      }),
    ).toBe(
      "/dashboard/develop/data-1?source=onboarding&step=fix-eval-failure&source_type=dataset&source_id=data-1&eval_id=eval-1&run_id=run-1&quick_start_goal=evaluate_quality&quick_start_id=evals&quick_start_primary_path=evals",
    );
    expect(
      buildEvalPostRepairHomeHref({
        previousRunId: "run-1",
        quickStartAttribution: attribution,
        rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
        runId: "run-2",
        sourceId: "data-1",
        sourceType: "dataset",
      }),
    ).toBe(
      "/dashboard/home?source=onboarding&target_event=first_quality_loop_completed&target_route=activation_home&run_id=run-2&source_type=dataset&source_id=data-1&rerun_from=source_fix&previous_run_id=run-1&quick_start_goal=evaluate_quality&quick_start_id=evals&quick_start_primary_path=evals",
    );
  });

  it("builds a post-repair quality home href", () => {
    const href = buildEvalPostRepairHomeHref({
      previousRunId: "run-1",
      rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
      runId: "run-2",
      sourceId: "project-1",
      sourceType: "trace_project",
    });
    const url = new URL(href, "http://localhost");

    expect(url.pathname).toBe("/dashboard/home");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      source: "onboarding",
      target_event: "first_quality_loop_completed",
      target_route: "activation_home",
      run_id: "run-2",
      source_type: "trace_project",
      source_id: "project-1",
      rerun_from: "source_fix",
      previous_run_id: "run-1",
    });
  });

  it("builds source-fix routes only when source context is actionable", () => {
    expect(
      buildEvalSourceFixHref({
        evalId: "eval-1",
        runId: "run-1",
        sourceId: "data-1",
        sourceType: "dataset",
      }),
    ).toBe(
      "/dashboard/develop/data-1?source=onboarding&step=fix-eval-failure&source_type=dataset&source_id=data-1&eval_id=eval-1&run_id=run-1",
    );
    expect(
      buildEvalSourceFixHref({
        runId: "run-1",
        sourceId: "project-1",
        sourceType: "trace_project",
      }),
    ).toBe(
      "/dashboard/observe/project-1/llm-tracing?source=onboarding&step=fix-eval-failure&source_type=trace_project&source_id=project-1&run_id=run-1",
    );
    expect(
      buildEvalSourceFixHref({
        runId: "run-1",
        sourceId: "sim-1",
        sourceType: "simulation",
      }),
    ).toBe(
      "/dashboard/simulate/test/sim-1/runs?source=onboarding&step=fix-eval-failure&source_type=simulation&source_id=sim-1&run_id=run-1",
    );
    expect(
      buildEvalSourceFixHref({
        runId: "run-1",
        sourceId: "unknown-1",
        sourceType: "unknown",
      }),
    ).toBeNull();
  });

  it("parses and describes eval source-fix destination params", () => {
    expect(
      getEvalSourceFixOnboardingParams(
        "?source=onboarding&step=fix-eval-failure&eval_id=eval-1&run_id=run-1&source_type=dataset&source_id=data-1",
      ),
    ).toEqual({
      evalId: "eval-1",
      isOnboarding: true,
      runId: "run-1",
      setupLanguage: null,
      setupProvider: null,
      sourceId: "data-1",
      sourceType: "dataset",
      step: "fix-eval-failure",
      traceId: null,
      tourAnchor: null,
    });

    expect(getEvalSourceFixOnboardingParams("?source=onboarding")).toEqual({
      evalId: null,
      isOnboarding: false,
      runId: null,
      setupLanguage: null,
      setupProvider: null,
      sourceId: null,
      sourceType: null,
      step: null,
      traceId: null,
      tourAnchor: null,
    });

    expect(
      getEvalSourceFixOnboardingCopy({ sourceType: "dataset" }),
    ).toMatchObject({
      title: "Fix the eval source",
      description:
        "Update the dataset row or expected output that produced the failed result, then rerun the quality check.",
    });
    expect(
      getEvalSourceFixOnboardingCopy({ sourceType: "simulation" }),
    ).toMatchObject({
      title: "Fix the simulation source",
      description:
        "Update the simulation scenario or expected behavior that produced this result, then rerun the quality check.",
    });
    expect(
      getEvalSourceFixOnboardingCopy({ sourceType: "trace_project" }),
    ).toMatchObject({
      title: "Fix trace source",
      description:
        "Review the traces or project setup that produced this quality-check result, then rerun the quality check.",
    });
  });

  it("extracts the safest available run id from eval run results", () => {
    expect(getEvalRunResultId({ log_id: "log-1" })).toBe("log-1");
    expect(
      getEvalRunResultId({
        eval_log_id: "eval-log-1",
        eval_task_id: "task-1",
        log_id: "log-1",
        run_id: "run-1",
      }),
    ).toBe("log-1");
    expect(
      getEvalRunResultId({
        eval_log_id: "eval-log-1",
        eval_task_id: "task-1",
        run_id: "run-1",
      }),
    ).toBe("eval-log-1");
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
    expect(getEvalUsageReviewOutcome({ status: "cancelled" })).toBe(
      "failure_reviewed",
    );
    expect(getEvalUsageReviewOutcome({ result: "Errored" })).toBe(
      "failure_reviewed",
    );
    expect(getEvalUsageReviewOutcome({ score: 0.4 })).toBe(
      "weak_result_reviewed",
    );
    expect(getEvalUsageReviewOutcome({ status: "running" })).toBe(
      "pending_result",
    );
    expect(getEvalUsageReviewOutcome({})).toBe("pending_result");
    expect(getEvalUsageReviewOutcome({ result: "Passed", score: 0.95 })).toBe(
      "result_summary_reviewed",
    );
  });

  it("chooses review actions from the usage outcome", () => {
    const sourceFixHref = "/dashboard/observe/project-1/llm-tracing";
    const scorerEditHref = "/dashboard/evaluations/create/eval-1";

    expect(
      getEvalReviewActionKind({
        log: { result: "Failed" },
        scorerEditHref,
        sourceFixHref,
      }),
    ).toBe(EVAL_REVIEW_ACTIONS.SOURCE_FIX);
    expect(
      getEvalReviewActionKind({
        log: { score: 0.4 },
        scorerEditHref,
        sourceFixHref,
      }),
    ).toBe(EVAL_REVIEW_ACTIONS.SOURCE_FIX);
    expect(
      getEvalReviewActionKind({
        canComplete: true,
        log: { status: "running" },
        scorerEditHref,
        sourceFixHref,
      }),
    ).toBeNull();
    expect(
      getEvalReviewActionKind({
        canComplete: true,
        log: { result: "Passed", score: 0.95 },
        scorerEditHref,
        sourceFixHref,
      }),
    ).toBe(EVAL_REVIEW_ACTIONS.COMPLETE);
    expect(
      getEvalReviewActionKind({
        log: { result: "Passed", score: 0.95 },
        scorerEditHref,
        sourceFixHref,
      }),
    ).toBe(EVAL_REVIEW_ACTIONS.SCORER_EDIT);
    expect(
      getEvalReviewActionKind({
        log: { result: "Failed" },
        scorerEditHref,
      }),
    ).toBe(EVAL_REVIEW_ACTIONS.SCORER_EDIT);
  });

  it("builds a review route focus payload", () => {
    expect(
      buildEvalReviewRouteFocusPayload({
        evalId: "eval-1",
        previousRunId: "run-0",
        rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
        route: "eval_detail",
        runId: "run-1",
        setupLanguage: "python",
        setupProvider: "anthropic",
        sourceId: "project-1",
        sourceType: "trace_project",
      }),
    ).toMatchObject({
      eventName: "onboarding_eval_route_focus_viewed",
      primaryPath: "evals",
      stage: "review_eval_failures",
      source: "eval_review_onboarding",
      artifactType: "eval_run",
      artifactId: "run-1",
      metadata: {
        eval_id: "eval-1",
        previous_run_id: "run-0",
        rerun_from: "source_fix",
        route: "eval_detail",
        run_id: "run-1",
        setup_language: "python",
        setup_provider: "anthropic",
        source_id: "project-1",
        source_type: "trace_project",
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
        sourceId: "project-1",
        sourceType: "trace_project",
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
        source_id: "project-1",
        source_type: "trace_project",
        step: "review",
        tab: "usage",
      },
      idempotencyKey: "eval_failures_reviewed:run-1:eval-1",
    });
  });

  it("builds a fix-rerun reviewed payload without captured content", () => {
    const payload = buildEvalFixRerunReviewedPayload({
      evalId: "eval-1",
      evalLogId: "log-2",
      previousRunId: "run-1",
      rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
      reviewOutcome: "result_summary_reviewed",
      rowSource: "dataset_evaluation",
      runId: "run-2",
      sourceId: "data-1",
      sourceType: "dataset",
    });

    expect(payload).toMatchObject({
      eventName: "onboarding_eval_fix_rerun_reviewed",
      primaryPath: "evals",
      stage: "review_eval_failures",
      source: "eval_review_onboarding",
      artifactType: "eval_run",
      artifactId: "run-2",
      metadata: {
        eval_id: "eval-1",
        eval_log_id: "log-2",
        previous_run_id: "run-1",
        rerun_from: "source_fix",
        review_outcome: "result_summary_reviewed",
        review_surface: "usage_log_detail",
        row_source: "dataset_evaluation",
        run_id: "run-2",
        source_id: "data-1",
        source_type: "dataset",
        step: "review",
        tab: "usage",
      },
      idempotencyKey:
        "onboarding_eval_fix_rerun_reviewed:source_fix:run-1:run-2",
    });
    expect(payload.metadata).not.toHaveProperty("output");
    expect(payload.metadata).not.toHaveProperty("reason");
    expect(payload.metadata).not.toHaveProperty("value");
  });

  it("parses eval failure action onboarding query params", () => {
    expect(
      getEvalFailureActionOnboardingParams(
        "?source=onboarding&step=fix-eval-failure&run_id=run-1&source_type=dataset&source_id=data-1&rerun_from=source_fix&previous_run_id=run-0",
      ),
    ).toEqual({
      isOnboarding: true,
      previousRunId: "run-0",
      rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
      runId: "run-1",
      setupLanguage: null,
      setupProvider: null,
      sourceId: "data-1",
      sourceType: "dataset",
      step: "fix-eval-failure",
      traceId: null,
      tourAnchor: null,
    });

    expect(getEvalFailureActionOnboardingParams("?source=onboarding")).toEqual({
      isOnboarding: false,
      previousRunId: null,
      rerunFrom: null,
      runId: null,
      setupLanguage: null,
      setupProvider: null,
      sourceId: null,
      sourceType: null,
      step: null,
      traceId: null,
      tourAnchor: null,
    });
  });

  it("parses eval source-fix journey-step params from Home CTAs", () => {
    expect(
      getEvalSourceFixOnboardingParams(
        "?tour_anchor=eval_next_loop_button&journey_step=eval_next_loop",
      ),
    ).toMatchObject({
      isOnboarding: true,
      step: "fix-eval-failure",
      tourAnchor: "eval_next_loop_button",
    });
  });

  it("builds an eval failure action payload without feedback content", () => {
    const payload = buildEvalFailureActionCreatedPayload({
      actionType: "recalculate",
      evalId: "eval-1",
      evalLogId: "log-1",
      feedbackId: "feedback-1",
      fixRoute: "/dashboard/develop/data-1?source=onboarding",
      rowSource: "eval_playground",
      runId: "run-1",
      sourceId: "data-1",
      sourceType: "dataset",
      step: "review",
    });

    expect(payload).toMatchObject({
      eventName: "eval_failure_action_created",
      primaryPath: "evals",
      stage: "eval_next_loop",
      source: "eval_review_onboarding",
      artifactType: "eval_run",
      artifactId: "feedback-1",
      metadata: {
        action_type: "recalculate",
        eval_id: "eval-1",
        eval_log_id: "log-1",
        feedback_id: "feedback-1",
        fix_route: "/dashboard/develop/data-1?source=onboarding",
        row_source: "eval_playground",
        run_id: "run-1",
        source_id: "data-1",
        source_type: "dataset",
        step: "review",
      },
      idempotencyKey: "eval_failure_action_created:feedback-1:eval-1",
    });
    expect(payload.metadata).not.toHaveProperty("value");
    expect(payload.metadata).not.toHaveProperty("explanation");
    expect(payload.metadata).not.toHaveProperty("reason");
  });

  it("builds a source-fix CTA payload without row content", () => {
    const payload = buildEvalSourceFixCtaClickedPayload({
      evalId: "eval-1",
      evalLogId: "log-1",
      fixRoute: "/dashboard/develop/data-1?source=onboarding",
      rowSource: "dataset_evaluation",
      runId: "run-1",
      sourceId: "data-1",
      sourceType: "dataset",
    });

    expect(payload).toMatchObject({
      eventName: "onboarding_eval_source_fix_cta_clicked",
      primaryPath: "evals",
      stage: "eval_next_loop",
      source: "eval_review_onboarding",
      artifactType: "dataset",
      artifactId: "data-1",
      metadata: {
        eval_id: "eval-1",
        eval_log_id: "log-1",
        fix_route: "/dashboard/develop/data-1?source=onboarding",
        row_source: "dataset_evaluation",
        run_id: "run-1",
        source_id: "data-1",
        source_type: "dataset",
        step: "fix-eval-failure",
      },
      idempotencyKey: "onboarding_eval_source_fix_cta_clicked:data-1:eval-1",
    });
    expect(payload.metadata).not.toHaveProperty("output");
    expect(payload.metadata).not.toHaveProperty("reason");
  });

  it("builds a source-fix route focus payload without source content", () => {
    const payload = buildEvalSourceFixRouteFocusPayload({
      evalId: "eval-1",
      route: "develop_dataset",
      runId: "run-1",
      sourceId: "data-1",
      sourceType: "dataset",
    });

    expect(payload).toMatchObject({
      eventName: "onboarding_eval_source_fix_route_viewed",
      primaryPath: "evals",
      stage: "eval_next_loop",
      source: "eval_review_onboarding",
      artifactType: "dataset",
      artifactId: "data-1",
      metadata: {
        eval_id: "eval-1",
        route: "develop_dataset",
        run_id: "run-1",
        source_id: "data-1",
        source_type: "dataset",
        step: "fix-eval-failure",
      },
      idempotencyKey: "onboarding_eval_source_fix_route_viewed:dataset:data-1",
    });
    expect(payload.metadata).not.toHaveProperty("rows");
    expect(payload.metadata).not.toHaveProperty("output");
    expect(payload.metadata).not.toHaveProperty("reason");
  });

  it("builds a source-fix rerun click payload without source content", () => {
    const payload = buildEvalSourceFixRerunClickedPayload({
      evalId: "eval-1",
      rerunRoute:
        "/dashboard/evaluations/create/eval-1?source=onboarding&step=run",
      route: "develop_dataset",
      runId: "run-1",
      sourceId: "data-1",
      sourceType: "dataset",
    });

    expect(payload).toMatchObject({
      eventName: "onboarding_eval_source_fix_rerun_clicked",
      primaryPath: "evals",
      stage: "eval_next_loop",
      source: "eval_review_onboarding",
      artifactType: "dataset",
      artifactId: "data-1",
      metadata: {
        eval_id: "eval-1",
        rerun_route:
          "/dashboard/evaluations/create/eval-1?source=onboarding&step=run",
        route: "develop_dataset",
        run_id: "run-1",
        source_id: "data-1",
        source_type: "dataset",
        step: "fix-eval-failure",
      },
      idempotencyKey: "onboarding_eval_source_fix_rerun_clicked:dataset:eval-1",
    });
    expect(payload.metadata).not.toHaveProperty("rows");
    expect(payload.metadata).not.toHaveProperty("output");
    expect(payload.metadata).not.toHaveProperty("reason");
  });

  it("builds a scorer edit CTA payload without row content", () => {
    const payload = buildEvalScorerEditCtaClickedPayload({
      editRoute:
        "/dashboard/evaluations/create/eval-1?source=onboarding&step=scorer",
      evalId: "eval-1",
      evalLogId: "log-1",
      rowSource: "eval_playground",
      runId: "run-1",
    });

    expect(payload).toMatchObject({
      eventName: "onboarding_eval_scorer_edit_cta_clicked",
      primaryPath: "evals",
      stage: "eval_next_loop",
      source: "eval_review_onboarding",
      artifactType: "eval_scorer",
      artifactId: "eval-1",
      metadata: {
        edit_route:
          "/dashboard/evaluations/create/eval-1?source=onboarding&step=scorer",
        eval_id: "eval-1",
        eval_log_id: "log-1",
        row_source: "eval_playground",
        run_id: "run-1",
        step: "scorer",
      },
      idempotencyKey: "onboarding_eval_scorer_edit_cta_clicked:eval-1:log-1",
    });
    expect(payload.metadata).not.toHaveProperty("output");
    expect(payload.metadata).not.toHaveProperty("reason");
    expect(payload.metadata).not.toHaveProperty("value");
  });

  it("builds an eval first quality loop completion payload after repair", () => {
    const payload = buildEvalFirstQualityLoopCompletedPayload({
      evalId: "eval-1",
      evalLogId: "log-2",
      previousRunId: "run-1",
      rerunFrom: EVAL_FIX_RERUN_ORIGINS.SOURCE_FIX,
      reviewOutcome: "result_summary_reviewed",
      runId: "run-2",
      sourceId: "project-1",
      sourceType: "trace_project",
    });

    expect(payload).toMatchObject({
      eventName: "first_quality_loop_completed",
      primaryPath: "evals",
      stage: "activated",
      source: "eval_review_onboarding",
      artifactType: "eval_run",
      artifactId: "run-2",
      metadata: {
        eval_id: "eval-1",
        eval_log_id: "log-2",
        previous_run_id: "run-1",
        rerun_from: "source_fix",
        review_outcome: "result_summary_reviewed",
        run_id: "run-2",
        source_id: "project-1",
        source_type: "trace_project",
        step: "review",
        tab: "usage",
      },
      idempotencyKey:
        "eval_onboarding:first_quality_loop_completed:run-1:run-2",
      isSample: false,
    });
    expect(payload.metadata).not.toHaveProperty("output");
    expect(payload.metadata).not.toHaveProperty("reason");
  });
});
