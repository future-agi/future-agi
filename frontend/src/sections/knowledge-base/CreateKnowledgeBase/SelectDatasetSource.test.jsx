import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import SelectDatasetSource from "./SelectDatasetSource";

const mockDatasets = [
  { id: "ds-1", name: "Support tickets" },
  { id: "ds-2", name: "FAQ dataset" },
];
const mockColumns = [
  { id: "col-1", name: "question" },
  { id: "col-2", name: "answer" },
];

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual("@tanstack/react-query");
  return {
    ...actual,
    useQuery: ({ queryKey, enabled }) => {
      if (queryKey[0] === "datasets") {
        return {
          data: { items: mockDatasets, total: mockDatasets.length },
          isLoading: false,
        };
      }
      if (queryKey[0] === "kb-dataset-columns") {
        return {
          data: enabled ? mockColumns : undefined,
          isLoading: false,
        };
      }
      return { data: undefined, isLoading: false };
    },
  };
});

describe("SelectDatasetSource", () => {
  it("disables the column picker until a dataset is chosen, and resets columns on dataset change", async () => {
    const user = userEvent.setup();
    const setDatasetId = vi.fn();
    const setColumnIds = vi.fn();

    render(
      <SelectDatasetSource
        datasetId={null}
        setDatasetId={setDatasetId}
        columnIds={[]}
        setColumnIds={setColumnIds}
      />,
    );

    expect(screen.getByPlaceholderText("Select a dataset first")).toBeDisabled();

    await user.click(screen.getByPlaceholderText("Select dataset"));
    await user.click(await screen.findByText("Support tickets"));

    expect(setDatasetId).toHaveBeenCalledWith("ds-1");
    expect(setColumnIds).toHaveBeenCalledWith([]);
  });

  it("passes the full selected array back through setColumnIds on multi-select", async () => {
    const user = userEvent.setup();
    const setColumnIds = vi.fn();

    render(
      <SelectDatasetSource
        datasetId="ds-1"
        setDatasetId={vi.fn()}
        columnIds={[]}
        setColumnIds={setColumnIds}
      />,
    );

    await user.click(screen.getByPlaceholderText("Select columns"));
    await user.click(await screen.findByText("question"));

    expect(setColumnIds).toHaveBeenCalledWith(["col-1"]);
  });
});
