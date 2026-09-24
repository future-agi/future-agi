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
