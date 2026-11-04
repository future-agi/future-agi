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
