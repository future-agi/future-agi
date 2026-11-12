import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import DatasetTestMode from "../DatasetTestMode";

vi.mock("src/utils/axios", async (original) => {
  const actual = await original();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});
vi.mock("../useMapToVariable", () => ({
  useMapToVariable: () => ({
    renderRowMapAction: () => null,
    mapMenu: null,
    rowHoverSx: {},
  }),
}));
vi.mock("../../hooks/useCompositeEval", () => ({
  useExecuteCompositeEvalAdhoc: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("../../hooks/useErrorLocalizerPoll", () => ({
  default: () => ({ state: {}, start: vi.fn() }),
}));
vi.mock("../EvalResultDisplay", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

beforeEach(() => vi.clearAllMocks());

const tableRow = (value) => ({
  "input-column": { cell_id: `cell-${value}`, cell_value: value },
});

describe("Dataset test mode row navigation (W10-EVALS-U02)", () => {
  it("names the previous and next row buttons", async () => {
    axios.get.mockImplementation(async (url) => {
      if (url === endpoints.develop.getDatasetDetail("dataset")) {
        return {
          data: {
            result: {
              column_config: [
                { id: "input-column", name: "input", data_type: "text" },
              ],
              table: [tableRow("First input"), tableRow("Second input")],
              metadata: { total_rows: 2 },
            },
          },
        };
      }
      return {
        data: {
          status: true,
          result: { datasets: [{ id: "dataset", name: "Dataset" }] },
        },
      };
    });
    render(
      <DatasetTestMode
        templateId="eval"
        initialDatasetId="dataset"
        variables={["input"]}
      />,
    );

    const next = await screen.findByRole("button", { name: "Next row" });
    const previous = screen.getByRole("button", { name: "Previous row" });
    expect(screen.getByText("Test on row 1 of 2")).toBeInTheDocument();
    expect(previous).toBeDisabled();
    expect(next).toBeEnabled();

    fireEvent.click(next);
    expect(screen.getByText("Test on row 2 of 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous row" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Next row" })).toBeDisabled();
  });
});
