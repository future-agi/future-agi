import React from "react";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, vi } from "vitest";

const ui = vi.hoisted(() => ({
  notify: vi.fn(),
  snackbar: vi.fn(),
  navigate: vi.fn(),
  submitted: vi.fn(),
  trackRuns: vi.fn(),
  refreshGrid: vi.fn(),
  nameAvailable: true,
  realNameCheck: false,
  blockAtValidation: false,
  emptyDefaults: false,
  columns: [],
  datasetEvals: [],
  runConfigs: undefined,
  sourceExperiment: null,
  evalChoice: [],
  sampleRows: [],
  sampleTotal: 1,
  suggestion: {},
  store: {
    openRunExperiment: true,
    currentStep: 3,
    visitedSteps: [true, true, true, true],
    nextStep: vi.fn(),
    prevStep: vi.fn(),
    setCurrentStep: vi.fn(),
    reset: vi.fn(),
    setLaunched: vi.fn(),
  },
}));
export { ui };
vi.mock("src/api/develop/experiment-create", async (original) => {
  const actual =
    /** @type {typeof import("src/api/develop/experiment-create")} */ (
      await original()
    );
  return {
    ...actual,
    useExperimentNameSuggestion: () => ({ data: ui.suggestion }),
    /** @param {Parameters<typeof actual.useExperimentNameCheck>} args */
    useExperimentNameCheck: (...args) =>
      ui.realNameCheck
        ? actual.useExperimentNameCheck(...args)
        : { data: ui.nameAvailable },
    useExperimentDatasetVersions: () => ({ data: [] }),
    useExperimentDatasetSplits: () => ({ data: { splits: [] } }),
    useDatasetEvalsForExperiment: () => ({ data: ui.datasetEvals }),
    // `sampleRows: null` is a sample that has not loaded yet.
    useExperimentSampleTable: () => ({
      data:
        ui.sampleRows === null
          ? undefined
          : { columns: ui.columns, rows: ui.sampleRows, total: ui.sampleTotal },
    }),
  };
});
vi.mock("react-router", () => ({
  useNavigate: () => ui.navigate,
  useParams: () => ({ dataset: "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3e" }),
}));
vi.mock("react-router-dom", () => ({
  useSearchParams: () => [new URLSearchParams("new_experiment=1"), vi.fn()],
}));
vi.mock("../../states", () => ({
  useRunExperimentStoreShallow: (select) => select(ui.store),
  useRunExperimentStore: { getState: () => ui.store },
}));
vi.mock("../../Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: ui.refreshGrid }),
}));
vi.mock("src/hooks/useGetExperimentDetails", () => ({
  useGetExperimentDetails: () => ({}),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useGetJsonColumnSchema: () => ({}),
  useDatasetDerivedVariables: () => ({}),
}));
vi.mock("src/api/develop/r2", () => ({
  useRunConfigs: () => ({ data: ui.runConfigs }),
}));
vi.mock("src/api/develop/runs", () => ({ useExperimentDetail: () => ({}) }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({}) }));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/utils/cacheUtils", () => ({
  invalidateDatasetListCache: vi.fn(),
}));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: ui.snackbar }));
vi.mock("src/sections/common/ux/toast", () => ({
  notify: ui.notify,
  MAX_TOAST_MS: 1,
}));
vi.mock("src/sections/common/ux/useShortcuts", () => ({ default: () => {} }));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({}),
}));
vi.mock("../runTrayStore", () => ({ trackRuns: ui.trackRuns, useRunTray: {} }));
vi.mock("../common", async () => {
  const { z } = await import("zod");
  const { promptConfigTransform } = await import("../utils");
  return {
    // Keep the real wire transform. A late schema change lets the guard test
    // reach blockedSubmit after the per-render check allowed the click.
    getNewExperimentValidationSchema: () =>
      z.any().transform((values) => {
        if (ui.blockAtValidation) values.trials = 3;
        return {
          ...values,
          promptConfig: promptConfigTransform(
            values.promptConfig,
            values.experimentType,
            values.outputFormat,
            false,
          ),
          userEvalMetrics: values.userEvalMetrics.map((metric) => ({
            name: metric.name,
            template_id: metric.templateId,
            config: metric.config,
          })),
        };
      }),
  };
});
vi.mock("../experimentFormValues", async (original) => {
  const actual = /** @type {typeof import("../experimentFormValues")} */ (
    await original()
  );
  return {
    ...actual,
    emptyExperimentValues: () =>
      ui.emptyDefaults
        ? actual.emptyExperimentValues()
        : {
            ...actual.emptyExperimentValues(),
            name: "Recovery experiment",
            userEvalMetrics: [
              {
                templateId: "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3d",
                name: "submission-score",
                config: { mapping: { output: "output" } },
              },
            ],
            promptConfig: [
              {
                experimentType: "llm",
                targetType: "prompt",
                name: "Saved prompt",
                promptId: "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3b",
                promptVersion: "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3c",
                model: [{ value: "gpt-4o-mini" }],
                modelParams: {},
                variables: [],
                variableMapping: {},
                configuration: { toolChoice: "auto", tools: [] },
              },
            ],
          },
  };
});
// Keep the real drawer, form state, mutation and wire body, not unrelated
// picker/estimate panels. Axios is deliberately NOT mocked by this fixture.
vi.mock("../StepperForExperiment", () => ({ default: () => null }));
vi.mock(
  "../StepperComponentExperiment/ConfigureStepExperimentCreation",
  async () => {
    const { useController, useWatch } = await import("react-hook-form");
    const { default: PropTypes } = await import("prop-types");
    const Configure = ({ control, nameState }) => {
      const { field } = useController({ control, name: "name" });
      const evaluations = useWatch({ control, name: "userEvalMetrics" });
      return (
        <>
          <input
            {...field}
            value={field.value ?? ""}
            aria-label="Experiment name"
            onFocus={nameState.onFocus}
          />
          <output aria-label="Configured evaluations">
            {JSON.stringify(evaluations)}
          </output>
        </>
      );
    };
    Configure.propTypes = {
      control: PropTypes.object,
      nameState: PropTypes.object,
    };
    return { default: Configure };
  },
);
vi.mock("../StepperComponentExperiment/DataStep", () => ({
  default: () => null,
}));
vi.mock(
  "../StepperComponentExperiment/EvaluationStepExperimentCreation",
  () => ({ default: () => null }),
);
vi.mock("../StepperComponentExperiment/ReviewStep", async () => {
  const { useController, useWatch } = await import("react-hook-form");
  const { default: PropTypes } = await import("prop-types");
  const Field = ({ control, name, label }) => {
    const { field } = useController({ control, name });
    return <input {...field} value={field.value ?? ""} aria-label={label} />;
  };
  Field.propTypes = {
    control: PropTypes.object.isRequired,
    name: PropTypes.string.isRequired,
    label: PropTypes.string.isRequired,
  };
  const Reference = ({ control }) => {
    const { field } = useController({ control, name: "columnId" });
    return (
      <select
        {...field}
        value={field.value ?? ""}
        onChange={(event) => field.onChange(event.target.value || null)}
        aria-label="Reference column"
      >
        <option value="">No reference column</option>
        {ui.columns.map((column) => (
          <option key={column.id} value={column.id}>
            {column.name}
          </option>
        ))}
      </select>
    );
  };
  Reference.propTypes = { control: PropTypes.object.isRequired };
  const Evaluations = ({ control }) => {
    const { field } = useController({ control, name: "userEvalMetrics" });
    return (
      <>
        <span data-testid="evaluations">
          {(field.value ?? []).map((metric) => metric.name).join(",")}
        </span>
        <button type="button" onClick={() => field.onChange(ui.evalChoice)}>
          Replace evaluations
        </button>
      </>
    );
  };
  Evaluations.propTypes = { control: PropTypes.object.isRequired };
  const StartFrom = ({ control }) => {
    const { field } = useController({ control, name: "startFrom" });
    const source = { experimentId: ui.sourceExperiment, carryOver: false };
    return (
      <button type="button" onClick={() => field.onChange(source)}>
        Start from source
      </button>
    );
  };
  StartFrom.propTypes = { control: PropTypes.object.isRequired };
  const Rows = ({ control }) => {
    const rowIds = useWatch({ control, name: "rowIds" });
    return <span data-testid="rows">{(rowIds ?? []).join(",")}</span>;
  };
  Rows.propTypes = { control: PropTypes.object.isRequired };
  return {
    default: ({ control }) => (
      <>
        <Rows control={control} />
        <StartFrom control={control} />
        <Evaluations control={control} />
        <Reference control={control} />
        <Field control={control} name="name" label="Experiment name" />
        <Field
          control={control}
          name="promptConfig.0.name"
          label="Prompt name"
        />
      </>
    ),
  };
});
vi.mock("../RunExperimentFormSkeleton", () => ({ default: () => null }));
vi.mock("../RunTray", () => ({ RunTrayDock: () => null }));
vi.mock("../WaitingForResultsPanel", () => ({ default: () => null }));
vi.mock("../RunActionConfirmHost", () => ({ default: () => null }));
vi.mock("../EstimateDetails", () => ({
  EstimateBreakdown: () => null,
  EstimateSummary: () => null,
  useEstimateDetails: () => ({}),
}));
vi.mock("../CostEstimateLine", () => ({ SimpleEstimate: () => null }));
vi.mock("../StartItBlock", () => ({ terminalRunsOf: () => [] }));
vi.mock("../StartFromExperiment", () => ({
  START_FROM_PRESET: "start-from",
  cardsFromExperiment: vi.fn(),
}));
vi.mock("../ExternalRunCard", () => ({ issues: () => [] }));
vi.mock("../LinkedTracesCard", () => ({ issues: () => [] }));
vi.mock("../HarnessRunCard", () => ({ issues: () => [] }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-dialog", () => ({ ConfirmDialog: () => null }));
// Keep the production discard dialog so close/reopen exercises its action.
vi.mock(
  "src/components/custom-audio/context-provider/AudioPlaybackContext",
  () => ({
    AudioPlaybackProvider: ({ children }) => children,
  }),
);
vi.mock("src/sections/develop-detail/DataTab/Versions/versionedSource", () => ({
  tableRequestFor: vi.fn(() => ({ url: "/dataset-rows", params: {} })),
}));
import RunExperiment from "../RunExperiment";

export const mountDrawer = () => {
  ui.store.openRunExperiment = true;
  ui.store.reset.mockImplementation(() => {
    ui.store.openRunExperiment = false;
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = () => (
    <QueryClientProvider client={client}>
      <RunExperiment onSuccessfulSubmit={ui.submitted} />
    </QueryClientProvider>
  );
  return { ...render(view()), view, client };
};
export const assertNoSuccessEffects = () => {
  expect(ui.notify).not.toHaveBeenCalled();
  expect(ui.navigate).not.toHaveBeenCalled();
  expect(ui.trackRuns).not.toHaveBeenCalled();
  expect(ui.submitted).not.toHaveBeenCalled();
  expect(ui.store.setLaunched).not.toHaveBeenCalled();
  expect(ui.refreshGrid).not.toHaveBeenCalled();
};
