/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import TestPlayground from "../TestPlayground";

vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { post: vi.fn() },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("../../hooks/useEvalVersions", () => ({
  useEvalVersions: () => ({}),
  useSetDefaultVersion: () => ({}),
}));
vi.mock("../../hooks/useCompositeEval", () => ({
  useExecuteCompositeEval: () => ({}),
  useExecuteCompositeEvalAdhoc: () => ({}),
}));
vi.mock("../../hooks/useErrorLocalizerPoll", () => ({
  default: () => ({ state: {}, start: vi.fn() }),
}));
vi.mock("src/hooks/use-credit-exhaustion", () => ({
  useCreditExhaustion: () => ({ handleError: vi.fn() }),
}));
vi.mock("../CodeEditor", () => ({ default: () => null }));
vi.mock("../EvalResultDisplay", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../DatasetTestMode", async () => ({
  default: (await import("react")).forwardRef(function DatasetPreview({
    model,
  }) {
    return <output data-testid="source-model">{model || "No model"}</output>;
  }),
}));
vi.mock("../TracingTestMode", async () => ({
  default: (await import("react")).forwardRef(function TracingPreview({
    model,
  }) {
    return <output data-testid="source-model">{model || "No model"}</output>;
  }),
}));
vi.mock("../SimulationTestMode", async () => ({
  default: (await import("react")).forwardRef(function SimulationPreview({
    model,
  }) {
    return <output data-testid="source-model">{model || "No model"}</output>;
  }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue({
    data: { status: true, result: { output: 1 } },
  });
});

describe("Code playground model suppression", () => {
  it("sends the current decision rubric as a runtime override without mutating the template", async () => {
    const ref = React.createRef();
    const model = "decision/openrouter/openai/gpt-6-luna-decisions";
    const initial = {
      model,
      jev_mapping: { score: { levels: ["Old", "Rubric"] } },
    };
    const next = {
      model,
      output_type: "score",
      jev_mapping: {
        revision: "jev-map-v1",
        question_type: "score",
        score: { levels: ["Incorrect", "Correct"] },
      },
    };
    const view = render(
      <TestPlayground
        ref={ref}
        templateId="eval"
        evalType="llm"
        model={model}
        runConfig={initial}
      />,
    );
    view.rerender(
      <TestPlayground
        ref={ref}
        templateId="eval"
        evalType="llm"
        model={model}
        runConfig={next}
      />,
    );
    await act(async () => ref.current.runTest());
    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    expect(axios.post).toHaveBeenCalledWith(
      endpoints.develop.eval.evalPlayground,
      expect.objectContaining({
        model,
        config: expect.objectContaining({ run_config: next }),
      }),
    );
  });

  it("ignores a stale Jev model when running a Code evaluation", async () => {
    const ref = React.createRef();
    render(
      <TestPlayground
        ref={ref}
        templateId="code"
        evalType="code"
        model="jev-latest"
      />,
    );
    await act(async () => {
      ref.current.runTest();
    });
    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    expect(axios.post).toHaveBeenCalledWith(
      endpoints.develop.eval.evalPlayground,
      expect.objectContaining({
        template_id: "code",
        config: expect.objectContaining({ params: {} }),
      }),
    );
    expect(axios.post.mock.calls[0][1]).not.toHaveProperty("model");
  });

  it.each(["Dataset", "Tracing", "Simulation"])(
    "passes no stale Jev model to %s Code testing",
    async (source) => {
      render(
        <TestPlayground templateId="code" evalType="code" model="jev-latest" />,
      );
      await userEvent.click(screen.getByRole("tab", { name: source }));
      expect(screen.getByTestId("source-model")).toHaveTextContent("No model");
    },
  );

  it("uses the current model on a new test even when inputs have not changed", async () => {
    const ref = React.createRef();
    const view = render(
      <TestPlayground
        ref={ref}
        templateId="eval"
        evalType="llm"
        model="jev-latest"
        instructions="Judge {{input}}"
      />,
    );
    view.rerender(
      <TestPlayground
        ref={ref}
        templateId="eval"
        evalType="llm"
        model="turing_large"
        instructions="Judge {{input}}"
      />,
    );
    await act(async () => {
      ref.current.runTest();
    });
    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    expect(axios.post.mock.calls[0][1].model).toBe("turing_large");
  });
});

describe("Test Evaluation refused by the rate limit", () => {
  const limit =
    "Evaluation rate limit reached. Wait for the usage window to reset before retrying.";

  it("shows the server's 429 message instead of asking for a retry", async () => {
    // The app's axios interceptor rejects with the flattened response body.
    axios.post.mockRejectedValueOnce({
      status: false,
      type: "rate_limit",
      code: "rate_limited",
      detail: limit,
      message: limit,
      error: limit,
      result: limit,
      statusCode: 429,
    });
    const ref = React.createRef();
    const onTestResult = vi.fn();
    render(
      <TestPlayground
        ref={ref}
        templateId="eval"
        evalType="llm"
        model="turing_large"
        onTestResult={onTestResult}
      />,
    );
    await act(async () => {
      ref.current.runTest();
    });
    expect(await screen.findByText(limit)).toBeInTheDocument();
    expect(onTestResult).toHaveBeenCalledWith(false, limit);
    expect(
      screen.queryByText("Failed to run evaluation. Please retry."),
    ).not.toBeInTheDocument();
  });
});
