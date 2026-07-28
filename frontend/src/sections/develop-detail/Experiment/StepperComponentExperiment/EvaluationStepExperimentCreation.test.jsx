import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";

import EvaluationStepExperimentCreation from "./EvaluationStepExperimentCreation";

// Keep the picker itself out of scope — these tests only care about how
// EvaluationStepExperimentCreation routes an onEvalAdded payload (TH-6979:
// local-only for a pure version pick, scoped API call for a dirty edit).
const { capturedOnEvalAdded } = vi.hoisted(() => ({
  capturedOnEvalAdded: { current: null },
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

const { mockAxiosPost, mockAxiosGet } = vi.hoisted(() => ({
  mockAxiosPost: vi.fn(),
  mockAxiosGet: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: mockAxiosPost, get: mockAxiosGet },
  endpoints: {
    develop: {
      eval: {
        editEval: (datasetId, evalId) =>
          `/model-hub/develops/${datasetId}/edit_and_run_user_eval/${evalId}/`,
      },
      optimizeDevelop: { columnInfo: "/model-hub/optimize-develop/column-info/" },
    },
  },
}));

const { mockEnqueueSnackbar } = vi.hoisted(() => ({
  mockEnqueueSnackbar: vi.fn(),
}));
vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: mockEnqueueSnackbar,
}));

const EXISTING_USER_EVAL_ID = "11111111-1111-4111-8111-111111111111";

const Harness = ({ isEditingExperiment, experimentId, snapshotDatasetId }) => {
  const { control } = useForm({
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
  return (
    <EvaluationStepExperimentCreation
      control={control}
      allColumns={[{ field: "output", headerName: "Output", dataType: "text" }]}
      errors={{}}
      isEditingExperiment={isEditingExperiment}
      experimentId={experimentId}
      snapshotDatasetId={snapshotDatasetId}
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

describe("EvaluationStepExperimentCreation — inline version pin routing (TH-6979)", () => {
  beforeEach(() => {
    mockAxiosPost.mockReset();
    mockAxiosGet.mockReset();
    mockEnqueueSnackbar.mockReset();
    mockAxiosGet.mockResolvedValue({ data: { result: [] } });
    mockAxiosPost.mockResolvedValue({
      data: { result: { id: EXISTING_USER_EVAL_ID, pinned_version_id: "v2-id" } },
    });
  });

  const openEditForExistingEval = async () => {
    // The single eval row renders exactly two icon buttons, edit then
    // delete (see the JSX order in EvaluationStepExperimentCreation).
    const iconButtons = document.querySelectorAll(".MuiIconButton-root");
    fireEvent.click(iconButtons[0]);
    // Wait for the picker drawer to actually open (editingEval populated) —
    // capturedOnEvalAdded is already non-null from the initial render, so
    // that alone isn't a reliable signal that the edit click landed.
    await waitFor(() =>
      expect(screen.getByTestId("eval-picker-drawer")).toBeInTheDocument(),
    );
  };

  it("does not call the API when only the pinned version changes (isDirty=false)", async () => {
    renderStep({
      isEditingExperiment: true,
      experimentId: "exp-1",
      snapshotDatasetId: "snap-1",
    });

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
      expect(mockAxiosPost).not.toHaveBeenCalled();
    });
  });

  it("fires the scoped edit-eval save when the config was actually edited (isDirty=true)", async () => {
    renderStep({
      isEditingExperiment: true,
      experimentId: "exp-1",
      snapshotDatasetId: "snap-1",
    });

    await openEditForExistingEval();

    capturedOnEvalAdded.current({
      templateId: "tpl-1",
      name: "toxicity-eval",
      templateType: "single",
      model: "gpt-4",
      mapping: { output: "output" },
      versionId: "v2-id",
      isDirty: true,
    });

    await waitFor(() => {
      expect(mockAxiosPost).toHaveBeenCalledTimes(1);
    });
    const [url, payload] = mockAxiosPost.mock.calls[0];
    expect(url).toBe(
      `/model-hub/develops/snap-1/edit_and_run_user_eval/${EXISTING_USER_EVAL_ID}/`,
    );
    expect(payload.pinned_version_id).toBe("v2-id");
    expect(payload.experiment_id).toBe("exp-1");
  });

  it("does not call the scoped save for a dirty edit when not editing a persisted experiment", async () => {
    renderStep({ isEditingExperiment: false });

    await openEditForExistingEval();

    capturedOnEvalAdded.current({
      templateId: "tpl-1",
      name: "toxicity-eval",
      templateType: "single",
      model: "gpt-4",
      mapping: { output: "output" },
      versionId: "v2-id",
      isDirty: true,
    });

    await waitFor(() => {
      expect(mockAxiosPost).not.toHaveBeenCalled();
    });
  });
});
