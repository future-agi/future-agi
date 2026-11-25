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

const mockOneRowDataset = () =>
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

describe("Code dataset playground model override", () => {
  it.each([
    "",
    "jev-latest",
    "decision/openrouter/openai/gpt-6-luna-decisions",
  ])("sends a model only when one was supplied (%s)", async (model) => {
    mockOneRowDataset();
    axios.post.mockResolvedValue({
      data: { status: true, result: { output: 1 } },
    });
    const ref = React.createRef();
    const runConfig = model.startsWith("decision/")
      ? {
          model,
          jev_mapping: { revision: "jev-map-v1", question_type: "noul" },
        }
      : undefined;
    render(
      <DatasetTestMode
        ref={ref}
        templateId="eval"
        initialDatasetId="dataset"
        model={model}
        runConfig={runConfig}
        variables={["input"]}
      />,
    );
    await screen.findByTitle("Example input");
    // The variable is auto-mapped in an effect after the row renders. The
    // real Run button waits for isReady (onReadyChange), so the test does too.
    await waitFor(() => expect(ref.current.isReady).toBe(true));
    await act(async () => {
      ref.current.runTest();
    });
    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    const payload = axios.post.mock.calls.at(-1)[1];
    expect(payload.config.mapping).toEqual({ input: "Example input" });
    if (runConfig) expect(payload.config.run_config).toEqual(runConfig);
    else expect(payload.config).not.toHaveProperty("run_config");
    if (model) expect(payload.model).toBe(model);
    else expect(payload).not.toHaveProperty("model");
  });
});

describe("Dataset preview refused by the rate limit", () => {
  it("shows the server's 429 message instead of asking for a retry", async () => {
    const limit =
      "Evaluation rate limit reached. Wait for the usage window to reset before retrying.";
    mockOneRowDataset();
    // The app's axios interceptor rejects with the flattened response body.
    axios.post.mockRejectedValue({
      status: false,
      code: "rate_limited",
      message: limit,
      result: limit,
      statusCode: 429,
    });
    const ref = React.createRef();
    const onTestResult = vi.fn();
    render(
      <DatasetTestMode
        ref={ref}
        templateId="eval"
        initialDatasetId="dataset"
        model="turing_large"
        variables={["input"]}
        onTestResult={onTestResult}
      />,
    );
    await screen.findByTitle("Example input");
    await waitFor(() => expect(ref.current.isReady).toBe(true));
    await act(async () => {
      ref.current.runTest();
    });
    expect(await screen.findByText(limit)).toBeInTheDocument();
    expect(onTestResult).toHaveBeenCalledWith(false, limit);
  });
});
