import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import EvalPickerConfig from "./EvalPickerConfig";
import EvalPickerProvider from "./context/EvalPickerProvider";

vi.mock("src/hooks/useCapabilities", () => ({
  CAPABILITY: { TURING_MODELS: "turing_models" },
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
  useFeatureDenied: () => false,
}));

vi.mock("src/api/evaluations/decision-models", () => ({
  default: () => ({ data: [] }),
  decisionModelsQueryKey: ["eval-decision-models"],
}));
vi.mock("src/sections/evals/components/ModelSelector", () => ({
  default: () => null,
  FAGI_MODEL_VALUES: new Set(),
}));

describe("EvalPickerConfig source mapping", () => {
  it("requires and saves an explicit rubric for a decision-model score binding", async () => {
    const onSave = vi.fn();
    const route = "decision/openrouter/openai/gpt-6-luna-decisions";
    render(
      <EvalPickerProvider
        source="dataset"
        sourceColumns={["input"]}
        onClose={() => {}}
      >
        <EvalPickerConfig
          evalData={{
            id: "eval",
            name: "score",
            eval_type: "llm",
            output_type: "percentage",
            required_keys: ["input"],
            config: { model: route },
          }}
          onBack={() => {}}
          onSave={onSave}
        />
      </EvalPickerProvider>,
    );
    expect(
      screen.getByRole("button", { name: "Add Evaluation" }),
    ).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Add level" }));
    await userEvent.type(screen.getByLabelText("Level 0"), "Incorrect");
    await userEvent.click(screen.getByRole("button", { name: "Add level" }));
    await userEvent.type(screen.getByLabelText("Level 1"), "Correct");
    await userEvent.click(
      screen.getByRole("button", { name: "Add Evaluation" }),
    );
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        model: route,
        config: expect.objectContaining({
          model: route,
          run_config: expect.objectContaining({
            model: route,
            jev_mapping: expect.objectContaining({
              score: { levels: ["Incorrect", "Correct"] },
            }),
          }),
        }),
      }),
    );
  });

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
