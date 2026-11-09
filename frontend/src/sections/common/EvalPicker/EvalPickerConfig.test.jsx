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

describe("EvalPickerConfig live variable extraction", () => {
  const baseEval = {
    id: "eval-template",
    name: "Live vars eval",
    eval_type: "llm",
    required_keys: [],
  };

  const renderConfig = (evalData, onSave = vi.fn()) => {
    const tree = (data) => (
      <EvalPickerProvider
        source="task"
        sourceColumns={[]}
        onSourceColumnSearchChange={() => {}}
        onClose={() => {}}
      >
        <EvalPickerConfig
          evalData={data}
          onBack={() => {}}
          onSave={onSave}
          isSaving={false}
        />
      </EvalPickerProvider>
    );
    const utils = render(tree(evalData));
    return {
      ...utils,
      onSave,
      update: (data) => utils.rerender(tree(data)),
    };
  };

  it("shows a placeholder added to instructions after mount", () => {
    const { update } = renderConfig({
      ...baseEval,
      instructions: "Check {{answer}}",
    });
    expect(screen.getByText("{{answer}}")).toBeInTheDocument();
    expect(screen.queryByText("{{newVar}}")).not.toBeInTheDocument();

    update({
      ...baseEval,
      instructions: "Check {{answer}} against {{newVar}}",
    });

    expect(screen.getByText("{{answer}}")).toBeInTheDocument();
    expect(screen.getByText("{{newVar}}")).toBeInTheDocument();
  });

  it("drops the last placeholder instead of falling back to stale requiredKeys", async () => {
    const user = userEvent.setup();
    const { update, onSave } = renderConfig({
      ...baseEval,
      required_keys: ["answer"],
      instructions: "Check {{answer}}",
    });
    await user.type(
      screen.getByPlaceholderText("Enter column name..."),
      "col_answer",
    );

    update({
      ...baseEval,
      required_keys: ["answer"],
      instructions: "No placeholders left",
    });

    expect(screen.queryByText("{{answer}}")).not.toBeInTheDocument();
    expect(screen.queryByText("Variable Mapping")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add Evaluation" }));
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ mapping: {} }),
    );
  });

  it.each([
    ["", "empty"],
    ["   \n ", "whitespace-only"],
  ])(
    "falls back to requiredKeys when instructions are %j (%s)",
    (instructions) => {
      renderConfig({
        ...baseEval,
        required_keys: ["input", "output"],
        instructions,
      });
      expect(screen.getByText("{{input}}")).toBeInTheDocument();
      expect(screen.getByText("{{output}}")).toBeInTheDocument();
    },
  );

  it("prunes removed keys from the saved mapping", async () => {
    const user = userEvent.setup();
    const { update, onSave } = renderConfig({
      ...baseEval,
      instructions: "{{old}} {{keep}}",
    });
    const inputs = screen.getAllByPlaceholderText("Enter column name...");
    await user.type(inputs[0], "col_old");
    await user.type(inputs[1], "col_keep");

    update({ ...baseEval, instructions: "{{keep}}" });

    expect(screen.queryByText("{{old}}")).not.toBeInTheDocument();
    expect(screen.getByText("{{keep}}")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add Evaluation" }));
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ mapping: { keep: "col_keep" } }),
    );
  });
});
