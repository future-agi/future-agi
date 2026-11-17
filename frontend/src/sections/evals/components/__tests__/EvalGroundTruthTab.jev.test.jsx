import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  save: vi.fn(),
  embed: vi.fn(),
  dataset: {},
  evalData: {
    id: "template-1",
    model: "jev-latest",
    config: { required_keys: ["answer"], rule_prompt: "Judge {{answer}}" },
  },
}));

vi.mock("notistack", () => ({ useSnackbar: () => ({ enqueueSnackbar: vi.fn() }) }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ role: "Admin" }) }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgTheme: () => undefined }));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("react-dropzone", () => ({
  useDropzone: () => ({
    getRootProps: () => ({}),
    getInputProps: () => ({}),
    isDragActive: false,
  }),
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({ data: [], isLoading: false }),
  useGetDatasetColumns: () => ({ data: [] }),
}));
vi.mock("../../hooks/useEvalDetail", () => ({
  useEvalDetail: () => ({ data: mocks.evalData }),
}));
vi.mock("../../hooks/useGroundTruth", () => ({
  useGroundTruthList: () => ({ data: { items: [mocks.dataset] }, isLoading: false }),
  useGroundTruthData: () => ({ data: { rows: [], columns: ["answer", "expected"] } }),
  useGroundTruthStatus: () => ({ data: undefined }),
  useDeleteGroundTruth: () => ({ isPending: false }),
  useUploadGroundTruth: () => ({ isPending: false }),
  useSaveGroundTruthSetup: () => ({ isPending: false, mutateAsync: mocks.save }),
  useTriggerEmbedding: () => ({ isPending: false, mutateAsync: mocks.embed }),
}));

import EvalGroundTruthTab from "../EvalGroundTruthTab";
import { buildJevMapping, getJevValidationErrors } from "../../utils/jevMapping";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.save.mockResolvedValue({});
  mocks.dataset = {
    id: "gt-1",
    name: "Reference answers",
    row_count: 1,
    columns: ["answer", "expected"],
    variable_mapping: { answer: "answer" },
    role_mapping: { output: "expected" },
    embedding_status: "completed",
    is_active: true,
    enabled: false,
  };
});
afterEach(cleanup);

describe("W10-EVALS-L02 Jev ground truth", () => {
  it("explains the incompatibility and prevents enabling calibration", () => {
    render(<EvalGroundTruthTab templateId="template-1" isJevModel />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Ground truth is not supported with Jev models",
    );
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.queryByText("Ready")).not.toBeInTheDocument();
    expect(mocks.save).not.toHaveBeenCalled();
    expect(mocks.embed).not.toHaveBeenCalled();
  });

  it("lets a legacy grounded Jev evaluation disable calibration without deleting data", async () => {
    mocks.dataset.enabled = true;
    render(<EvalGroundTruthTab templateId="template-1" isJevModel onSwitchToDetails={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Test eval" })).toBeDisabled();
    const toggle = screen.getByRole("checkbox");
    expect(toggle).toBeEnabled();
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocks.save).toHaveBeenCalledWith({
      gtId: "gt-1",
      variableMapping: { answer: "answer" },
      roleMapping: { output: "expected" },
      maxExamples: 3,
      enabled: false,
    }));
    expect(mocks.embed).not.toHaveBeenCalled();
  });

  it("keeps the enable control available for other evaluators", () => {
    render(<EvalGroundTruthTab templateId="template-1" isJevModel={false} />);
    expect(screen.getByRole("checkbox")).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("blocks switching an already grounded evaluation to Jev", () => {
    const args = {
      mapping: buildJevMapping("pass_fail", null),
      config: {},
      groundTruthEnabled: true,
    };
    expect(getJevValidationErrors(args).join(" ")).toContain(
      "Ground truth is not supported with Jev models",
    );
    expect(getJevValidationErrors({ ...args, groundTruthEnabled: false })).toEqual([]);
  });
});
