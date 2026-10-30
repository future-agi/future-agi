import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "src/utils/test-utils";
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

describe("Code dataset playground model override", () => {
  it.each(["", "jev-latest"])(
    "sends a model only when one was supplied (%s)",
    async (model) => {
      axios.get.mockImplementation(async (url) => {
        if (url === endpoints.develop.getDatasetDetail("dataset")) {
          return {
            data: {
              result: {
                column_config: [
                  { id: "input-column", name: "input", data_type: "text" },
                ],
                table: [
                  {
                    "input-column": {
                      cell_id: "cell",
                      cell_value: "Example input",
                    },
                  },
                ],
                metadata: { total_rows: 1 },
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
      axios.post.mockResolvedValue({
        data: { status: true, result: { output: 1 } },
      });
      const ref = React.createRef();
      render(
        <DatasetTestMode
          ref={ref}
          templateId="eval"
          initialDatasetId="dataset"
          model={model}
          variables={["input"]}
        />,
      );
      await screen.findByTitle("Example input");
      await act(async () => {
        ref.current.runTest();
      });
      await waitFor(() => expect(axios.post).toHaveBeenCalled());
      const payload = axios.post.mock.calls.at(-1)[1];
      expect(payload.config.mapping).toEqual({ input: "Example input" });
      if (model) expect(payload.model).toBe(model);
      else expect(payload).not.toHaveProperty("model");
    },
  );
});
