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
    unsavedDefinition,
  }) {
    return (
      <output data-testid="source-definition">
        {unsavedDefinition ? JSON.stringify(unsavedDefinition) : "None"}
      </output>
    );
  }),
}));
vi.mock("../TracingTestMode", async () => ({
  default: (await import("react")).forwardRef(function TracingPreview({
    unsavedDefinition,
  }) {
    return (
      <output data-testid="source-definition">
        {unsavedDefinition ? JSON.stringify(unsavedDefinition) : "None"}
      </output>
    );
  }),
}));
vi.mock("../SimulationTestMode", async () => ({
  default: (await import("react")).forwardRef(function SimulationPreview({
    unsavedDefinition,
  }) {
    return (
      <output data-testid="source-definition">
        {unsavedDefinition ? JSON.stringify(unsavedDefinition) : "None"}
      </output>
    );
  }),
}));

const UNSAVED = {
  model: "claude-sonnet-4-5",
  instructions: "Judge {{input}} kindly",
  messages: [{ role: "system", content: "Judge {{input}} kindly" }],
};

beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue({
    data: { status: true, result: { output: "Passed" } },
  });
});

describe("Playground Test with unsaved changes", () => {
  it("sends the editor's unsaved definition with a custom-input test", async () => {
    const ref = React.createRef();
    render(
      <TestPlayground
        ref={ref}
        templateId="judge"
        evalType="llm"
        model="claude-sonnet-4-5"
        instructions="Judge {{input}} kindly"
        unsavedDefinition={UNSAVED}
      />,
    );
    await act(async () => {
      ref.current.runTest();
    });
    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    expect(axios.post).toHaveBeenCalledWith(
      endpoints.develop.eval.evalPlayground,
      expect.objectContaining({
        template_id: "judge",
        model: "claude-sonnet-4-5",
        config: expect.objectContaining({ unsaved_definition: UNSAVED }),
      }),
    );
  });

  it("sends no unsaved definition when the page has none", async () => {
    const ref = React.createRef();
    render(
      <TestPlayground
        ref={ref}
        templateId="system-judge"
        evalType="llm"
        model="turing_large"
        instructions="Judge {{input}}"
      />,
    );
    await act(async () => {
      ref.current.runTest();
    });
    await waitFor(() => expect(axios.post).toHaveBeenCalled());
    expect(axios.post.mock.calls[0][1].config).not.toHaveProperty(
      "unsaved_definition",
    );
  });

  it.each(["Dataset", "Tracing", "Simulation"])(
    "hands the unsaved definition to %s testing",
    async (source) => {
      render(
        <TestPlayground
          templateId="judge"
          evalType="llm"
          model="claude-sonnet-4-5"
          unsavedDefinition={UNSAVED}
        />,
      );
      await userEvent.click(screen.getByRole("tab", { name: source }));
      expect(screen.getByTestId("source-definition")).toHaveTextContent(
        JSON.stringify(UNSAVED),
      );
    },
  );
});
