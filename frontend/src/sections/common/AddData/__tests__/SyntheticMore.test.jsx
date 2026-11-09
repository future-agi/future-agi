import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ImmediateGenerateRows from "../ImmediateGenerateRows";
import SyntheticMoreAction from "../SyntheticMoreAction";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  refreshGrid: vi.fn(),
  shown: true,
  open: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({ default: mocks }));
vi.mock("src/launch/launchSwitch", () => ({
  isLaunchBuild: () => true,
  isSurfaceShown: () => mocks.shown,
}));
vi.mock("../GenerateRowsDialog", () => ({
  useGenerateRows: { getState: () => ({ open: mocks.open }) },
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ refreshGrid: mocks.refreshGrid }),
}));
vi.mock("src/components/custom-model-dropdown/CustomModelDropdown", () => ({
  default: ({ value, onChange }) => (
    <input
      aria-label="Model"
      value={value}
      onChange={(event) =>
        onChange({ target: { value: { model_name: event.target.value } } })
      }
    />
  ),
}));
const saved = {
  generation_mode: "append",
  model: "configured-model",
  num_rows: 2,
  reference_row_ids: ["old-row"],
  kb_id: "owned-kb",
  dataset: {
    name: "Questions",
    description: "Refunds",
    objective: "Support",
    patterns: "Short",
  },
  columns: ["question", "context", "citation"].map((name) => ({
    name,
    data_type: "text",
    description: name,
    property: [],
  })),
};
function mount(config = saved, rowIds = ["selected-row"]) {
  mocks.get.mockImplementation((path) =>
    Promise.resolve({
      data: {
        result: path.includes("knowledge-base")
          ? { table_data: [{ id: "owned-kb", name: "Support" }] }
          : path.includes("synthetic-config")
            ? { data: config ?? {} }
            : {
                metadata: { dataset_name: "Questions" },
                column_config: saved.columns.map((column) => ({
                  ...column,
                  id: column.name,
                  origin_type: "OTHERS",
                })),
              },
      },
    }),
  );
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ImmediateGenerateRows
        request={{ datasetId: "dataset", rowIds }}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.shown = true;
  mocks.post.mockResolvedValue({});
  mocks.put.mockResolvedValue({});
});
describe("selected-row and grounded generation", () => {
  it("uses the current selection for a new batch but exactly the saved sources for repeat", async () => {
    mount();
    await screen.findByText(
      "Using only 1 selected row as examples. Maximum 50.",
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Generate and save" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Generate and save" }));
    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/model-hub/develops/dataset/add_synthetic_data/",
        {
          ...saved,
          reference_row_ids: ["selected-row"],
        },
      ),
    );
    // The refetch restores the stored config; unsaved edits never affect its replay.
    await waitFor(() =>
      expect(
        screen.getByRole("button", {
          name: "Re-generate with saved configuration",
        }),
      ).toBeEnabled(),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Description" }), {
      target: { value: "unsaved" },
    });
    fireEvent.click(
      screen.getByRole("button", {
        name: "Re-generate with saved configuration",
      }),
    );
    await waitFor(() =>
      expect(mocks.put).toHaveBeenCalledWith(
        "/model-hub/develops/dataset/update-synthetic-config/",
        {
          ...saved,
          reference_row_ids: ["selected-row"],
          regenerate: true,
        },
      ),
    );
  });
  it("hides grounded controls and saved-source replay while the surface is off", async () => {
    mocks.shown = false;
    mount(saved, []);
    await screen.findByDisplayValue("Refunds");
    expect(
      screen.queryByRole("combobox", { name: "Knowledge base" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "Re-generate with saved configuration",
      }),
    ).not.toBeInTheDocument();
    expect(
      mocks.get.mock.calls.some(([url]) => url.includes("knowledge-base")),
    ).toBe(false);
  });
  it("offers the selected-row door only under its own gate and resolves exact IDs", async () => {
    const resolveRows = vi.fn((fn) => fn([{ row_id: "selected-row" }]));
    const view = render(
      <SyntheticMoreAction
        datasetId="dataset"
        count={1}
        resolveRows={resolveRows}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Generate more like these (1)" }),
    );
    expect(mocks.open).toHaveBeenCalledWith({
      datasetId: "dataset",
      rowIds: ["selected-row"],
    });
    mocks.shown = false;
    view.rerender(
      <SyntheticMoreAction
        datasetId="dataset"
        count={1}
        resolveRows={resolveRows}
      />,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
