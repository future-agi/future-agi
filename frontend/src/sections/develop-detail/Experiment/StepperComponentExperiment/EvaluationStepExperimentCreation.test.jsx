/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";

import EvaluationStepExperimentCreation from "./EvaluationStepExperimentCreation";

// Picker owns minting (versions/create). This host only pins whatever
// versionId the picker returns — same for create and edit.
const { capturedOnEvalAdded, formApi } = vi.hoisted(() => ({
  capturedOnEvalAdded: { current: null },
  formApi: { current: null },
}));
vi.mock("src/sections/common/EvalPicker", () => ({
  EvalPickerDrawer: ({ open, onEvalAdded }) => {
    capturedOnEvalAdded.current = onEvalAdded;
    return open ? <div data-testid="eval-picker-drawer" /> : null;
  },
}));

vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldControl: () => <div data-testid="column-select" />,
}));

const { mockAxiosPost, mockAxiosGet, mockAxiosPut } = vi.hoisted(() => ({
  mockAxiosPost: vi.fn(),
  mockAxiosGet: vi.fn(),
  mockAxiosPut: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: mockAxiosPost, get: mockAxiosGet, put: mockAxiosPut },
  endpoints: {
    develop: {
      optimizeDevelop: { columnInfo: "/model-hub/optimize-develop/column-info/" },
    },
  },
}));

const EXISTING_USER_EVAL_ID = "11111111-1111-4111-8111-111111111111";

const Harness = ({ isEditingExperiment }) => {
  const form = useForm({
    defaultValues: {
      userEvalMetrics: [
        {
          evalId: EXISTING_USER_EVAL_ID,
          actualEvalCreatedId: EXISTING_USER_EVAL_ID,
          name: "toxicity-eval",
          templateId: "tpl-1",
          templateType: "single",
          model: "gpt-4",
          mapping: { output: "output" },
          config: { mapping: { output: "output" } },
          pinnedVersionId: "v1-id",
        },
      ],
    },
  });
  formApi.current = form;
  return (
    <EvaluationStepExperimentCreation
      control={form.control}
      allColumns={[{ field: "output", headerName: "Output", dataType: "text" }]}
      errors={{}}
      isEditingExperiment={isEditingExperiment}
    />
  );
};

const renderStep = (props) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Harness {...props} />
    </QueryClientProvider>,
  );
};

describe("EvaluationStepExperimentCreation — version pin (create + edit)", () => {
  beforeEach(() => {
    mockAxiosPost.mockReset();
    mockAxiosGet.mockReset();
    mockAxiosPut.mockReset();
    formApi.current = null;
    mockAxiosGet.mockResolvedValue({ data: { result: [] } });
  });

  const openEditForExistingEval = async () => {
    const iconButtons = document.querySelectorAll(".MuiIconButton-root");
    fireEvent.click(iconButtons[0]);
    await waitFor(() =>
      expect(screen.getByTestId("eval-picker-drawer")).toBeInTheDocument(),
    );
  };

  it.each([true, false])(
    "pins the picker versionId locally and never calls edit_and_run (isEditing=%s)",
    async (isEditingExperiment) => {
      renderStep({ isEditingExperiment });

      await openEditForExistingEval();

      capturedOnEvalAdded.current({
        templateId: "tpl-1",
        name: "toxicity-eval",
        templateType: "single",
        model: "gpt-4",
        mapping: { output: "output" },
        versionId: "v2-id",
        isDirty: false,
      });

      await waitFor(() => {
        expect(formApi.current.getValues("userEvalMetrics")[0].pinnedVersionId).toBe(
          "v2-id",
        );
      });
      expect(mockAxiosPost).not.toHaveBeenCalled();
      expect(mockAxiosPut).not.toHaveBeenCalled();
    },
  );

  it.each([true, false])(
    "pins a freshly minted versionId the same way when dirty (isEditing=%s)",
    async (isEditingExperiment) => {
      renderStep({ isEditingExperiment });

      await openEditForExistingEval();

      // Picker already minted before onEvalAdded; host just stores the id.
      capturedOnEvalAdded.current({
        templateId: "tpl-1",
        name: "toxicity-eval",
        templateType: "single",
        model: "gpt-4",
        mapping: { output: "output" },
        versionId: "minted-v3-id",
        isDirty: false,
      });

      await waitFor(() => {
        expect(formApi.current.getValues("userEvalMetrics")[0].pinnedVersionId).toBe(
          "minted-v3-id",
        );
      });
      expect(mockAxiosPost).not.toHaveBeenCalled();
    },
  );

  it("re-editing a local eval (no experiment save yet) still updates the same row", async () => {
    renderStep({ isEditingExperiment: false });

    await openEditForExistingEval();
    capturedOnEvalAdded.current({
      templateId: "tpl-1",
      name: "toxicity-eval",
      templateType: "single",
      model: "gpt-4",
      mapping: { output: "output" },
      versionId: "v2-id",
      isDirty: false,
    });
    await waitFor(() => {
      expect(formApi.current.getValues("userEvalMetrics")[0].pinnedVersionId).toBe(
        "v2-id",
      );
    });

    // Re-open edit on the same row and pin another version — must not
    // silently no-op (regression: spreading RHF field.id into update()).
    await openEditForExistingEval();
    capturedOnEvalAdded.current({
      templateId: "tpl-1",
      name: "toxicity-eval",
      templateType: "single",
      model: "gpt-4o",
      mapping: { output: "output" },
      versionId: "v3-id",
      isDirty: false,
    });

    await waitFor(() => {
      const row = formApi.current.getValues("userEvalMetrics")[0];
      expect(row.pinnedVersionId).toBe("v3-id");
      expect(row.model).toBe("gpt-4o");
      expect(formApi.current.getValues("userEvalMetrics")).toHaveLength(1);
    });
  });

  it("nests run_config and dual-reads composite_weight_overrides from the picker", async () => {
    renderStep({ isEditingExperiment: false });

    await openEditForExistingEval();
    capturedOnEvalAdded.current({
      templateId: "tpl-1",
      name: "toxicity-eval",
      templateType: "composite",
      mapping: { output: "Output" },
      versionId: "comp-v1",
      error_localizer_enabled: true,
      // Picker emits snake; host must not drop it.
      composite_weight_overrides: { child_a: 0.6, child_b: 0.4 },
    });

    await waitFor(() => {
      const row = formApi.current.getValues("userEvalMetrics")[0];
      expect(row.config.run_config).toEqual({
        error_localizer_enabled: true,
      });
      expect(row.compositeWeightOverrides).toEqual({
        child_a: 0.6,
        child_b: 0.4,
      });
      expect(row.pinnedVersionId).toBe("comp-v1");
    });
  });
});
