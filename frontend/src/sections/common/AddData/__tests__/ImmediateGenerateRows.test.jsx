import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ImmediateGenerateRows from "../ImmediateGenerateRows";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  refreshGrid: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({ default: mocks }));
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
const config = {
  generation_mode: "append",
  model: "configured-model",
  num_rows: 2,
  dataset: {
    name: "Questions",
    description: "Refunds",
    objective: "Support",
    patterns: "Short",
  },
  columns: [
    {
      name: "question",
      data_type: "text",
      description: "question",
      property: [],
    },
  ],
};
function mount(saved = null) {
  mocks.get.mockImplementation((path) =>
    Promise.resolve({
      data: {
        result: path.includes("synthetic-config")
          ? { data: saved ?? {} }
          : {
              metadata: { dataset_name: "Questions" },
              column_config: [
                {
                  id: "col",
                  name: "question",
                  data_type: "text",
                  origin_type: "OTHERS",
                },
              ],
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
        request={{ datasetId: "dataset", rowIds: [] }}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.post.mockResolvedValue({});
  mocks.put.mockResolvedValue({});
});
describe("immediate row generation", () => {
  it("loads and reuses exactly the saved configuration despite unsaved edits", async () => {
    mount(config);
    await screen.findByRole("button", {
      name: "Re-generate with saved configuration",
    });
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
        { ...config, regenerate: true },
      ),
    );
    expect(mocks.post).not.toHaveBeenCalled();
    expect(mocks.refreshGrid).toHaveBeenCalledWith();
  });
  it("saves immediately through the classic writer and prevents duplicate submits", async () => {
    /** @type {(value: unknown) => void} */
    let finish;
    mocks.post.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    mount();
    await waitFor(() =>
      expect(
        screen.queryByText("Loading configuration…"),
      ).not.toBeInTheDocument(),
    );
    fireEvent.change(screen.getByRole("spinbutton", { name: "How many" }), {
      target: { value: "2" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Description" }), {
      target: { value: "Refunds" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Model" }), {
      target: { value: "configured-model" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Generate and save" }));
    fireEvent.click(screen.getByRole("button", { name: "Generate and save" }));
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post).toHaveBeenCalledWith(
      "/model-hub/develops/dataset/add_synthetic_data/",
      {
        ...config,
        dataset: { ...config.dataset, objective: "", patterns: "" },
      },
    );
    finish({});
    await screen.findByText(
      "2 generated rows saved. Existing rows are unchanged.",
    );
    expect(
      mocks.get.mock.calls.every(([path]) => !path.includes("imports")),
    ).toBe(true);
  });
});
