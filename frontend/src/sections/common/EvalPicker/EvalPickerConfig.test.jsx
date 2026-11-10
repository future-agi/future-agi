import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import EvalPickerConfig from "./EvalPickerConfig";
import EvalPickerProvider from "./context/EvalPickerProvider";

vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITY: { TURING_MODELS: "turing_models" },
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
}));

vi.mock("src/sections/evals/components/ModelSelector", () => ({
  FAGI_MODEL_VALUES: new Set(),
}));

describe("EvalPickerConfig source mapping", () => {
  it("accepts an arbitrary typed path and forwards it to exact search", async () => {
    const onSave = vi.fn();
    const onSourceColumnSearchChange = vi.fn();
    const user = userEvent.setup();

    render(
      <EvalPickerProvider
        source="task"
        sourceColumns={[{ field: "spans.0.foo", headerName: "spans.0.foo" }]}
        onSourceColumnSearchChange={onSourceColumnSearchChange}
        onClose={() => {}}
      >
        <EvalPickerConfig
          evalData={{
            id: "eval-template",
            name: "Typed path eval",
            eval_type: "code",
            required_keys: ["input"],
          }}
          onBack={() => {}}
          onSave={onSave}
          isSaving={false}
        />
      </EvalPickerProvider>,
    );

    const mappingInput = screen.getByPlaceholderText(
      "Select or enter column...",
    );
    await user.type(mappingInput, "spans.777.foo");

    expect(onSourceColumnSearchChange).toHaveBeenLastCalledWith(
      "spans.777.foo",
    );
    await user.click(screen.getByRole("button", { name: "Add Evaluation" }));
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        mapping: { input: "spans.777.foo" },
      }),
    );
  });
});

describe("EvalPickerConfig variable mapping", () => {
  const configTree = (evalData) => (
    <EvalPickerProvider source="task" onClose={() => {}}>
      <EvalPickerConfig
        evalData={evalData}
        onBack={() => {}}
        onSave={() => {}}
        isSaving={false}
      />
    </EvalPickerProvider>
  );

  it("lists a variable from the instructions that is missing from required keys", () => {
    render(
      configTree({
        id: "eval-template",
        name: "Live instructions eval",
        eval_type: "llm",
        required_keys: [],
        instructions: "Compare {{newVar}} with {{output}}",
      }),
    );

    expect(screen.getByText("Variable Mapping")).toBeInTheDocument();
    expect(screen.getByText("{{newVar}}")).toBeInTheDocument();
    expect(screen.getByText("{{output}}")).toBeInTheDocument();
  });

  it("drops a variable once it is removed from the instructions", () => {
    const evalData = {
      id: "eval-template",
      name: "Live instructions eval",
      eval_type: "llm",
      required_keys: ["newVar", "output"],
      instructions: "Compare {{newVar}} with {{output}}",
    };
    const { rerender } = render(configTree(evalData));
    expect(screen.getByText("{{newVar}}")).toBeInTheDocument();

    rerender(configTree({ ...evalData, instructions: "Score {{output}}" }));

    expect(screen.queryByText("{{newVar}}")).not.toBeInTheDocument();
    expect(screen.getByText("{{output}}")).toBeInTheDocument();
  });

  it("falls back to required keys when there is no instructions text", () => {
    render(
      configTree({
        id: "eval-template",
        name: "Stored keys eval",
        eval_type: "llm",
        required_keys: ["input"],
      }),
    );

    expect(screen.getByText("{{input}}")).toBeInTheDocument();
  });
});
