// Regression tests for the experiment snake_case hotfix: Run Experiment drawer.
// The payloads are the backend's own snake_case shapes; see
// src/sections/experiment-detail/__tests__/experiment-snake-case.fixtures.js.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { useWatch } from "react-hook-form";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  DATASET_ID,
  suggestNameResponse,
  validateNameTakenResponse,
} from "src/sections/experiment-detail/__tests__/experiment-snake-case.fixtures";

const axiosGetMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: { get: axiosGetMock, post: vi.fn(), put: vi.fn() },
  endpoints: {
    develop: {
      experiment: {
        suggestName: (datasetId) =>
          `/model-hub/experiments/v2/suggest-name/${datasetId}/`,
        validateName: "/model-hub/experiments/v2/validate-name/",
        create: () => "/model-hub/experiments/v2/",
        update: (id) => `/model-hub/experiments/v2/${id}/`,
        rerun: "/model-hub/experiments/v2/re-run/",
      },
    },
  },
}));

vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: vi.fn() }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDatasetColumnConfig: () => [],
  useDatasetDerivedVariables: () => ({ data: {} }),
  useGetJsonColumnSchema: () => ({ data: {} }),
  useColumnInfo: () => ({ data: [], isLoading: false }),
}));
vi.mock("src/api/experiment/use-experiment-derived-variables", () => ({
  useExperimentDerivedVariables: () => ({ data: {} }),
}));
vi.mock("src/api/experiment/use-get-exp-jsonschema", () => ({
  useGetExperimentJSONSchema: () => ({ data: {} }),
}));
vi.mock("src/hooks/useGetExperimentDetails", () => ({
  useGetExperimentDetails: () => ({ data: undefined, isLoading: false }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/sections/develop-detail/Experiment/StepperForExperiment", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/ConfigureStepExperimentCreation",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/EvaluationStepExperimentCreation",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/develop-detail/Experiment/StepperComponentExperiment/BasicStepExperimentCreation",
  () => ({
    default: function NameProbe({ control }) {
      const name = useWatch({ control, name: "name" });
      return <output aria-label="experiment name">{name}</output>;
    },
  }),
);

import RunExperiment from "src/sections/develop-detail/Experiment/RunExperiment";
import { useRunExperimentStore } from "src/sections/develop-detail/states";
import { getNewExperimentValidationSchema } from "src/sections/develop-detail/Experiment/schema";

describe("Run Experiment drawer reads the backend's snake_case keys", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
  });

  it("D1: prefills the name from suggest-name's `suggested_name`", async () => {
    // views/experiments.py:6324 returns {"suggested_name": ...}
    axiosGetMock.mockResolvedValue({ data: suggestNameResponse });
    useRunExperimentStore.setState({
      openRunExperiment: true,
      selectedExperiment: null,
      currentStep: 0,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[`/dashboard/develop/${DATASET_ID}`]}>
          <Routes>
            <Route
              path="/dashboard/develop/:dataset"
              element={<RunExperiment />}
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(axiosGetMock).toHaveBeenCalledWith(
        `/model-hub/experiments/v2/suggest-name/${DATASET_ID}/`,
      ),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("experiment name")).toHaveTextContent(
        "support-tickets-2026-10-22-v2",
      ),
    );
  });

  it("D2: rejects a taken name when validate-name answers `is_valid: false`", async () => {
    // views/experiments.py:6360-6363 returns {"is_valid": False, "message": ...}
    axiosGetMock.mockResolvedValue({ data: validateNameTakenResponse });
    const schema = getNewExperimentValidationSchema([], false, DATASET_ID);

    const result = await schema.safeParseAsync({
      columnId: null,
      name: "support-replies-oct",
      experimentType: "llm",
      outputFormat: "string",
      promptConfig: [],
      userEvalMetrics: [],
    });

    expect(axiosGetMock).toHaveBeenCalledWith(
      "/model-hub/experiments/v2/validate-name/",
      { params: { dataset_id: DATASET_ID, name: "support-replies-oct" } },
    );
    const nameIssues = (result.error?.issues ?? []).filter(
      (issue) => issue.path[0] === "name",
    );
    expect(nameIssues.map((issue) => issue.message)).toContain(
      "Experiment name already exists. Please choose another name.",
    );
  });
});
