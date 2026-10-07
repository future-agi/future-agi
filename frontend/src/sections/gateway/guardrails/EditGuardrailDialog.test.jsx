import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";
import EditGuardrailDialog from "./EditGuardrailDialog";

const mutate = vi.fn();

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useUpdateGuardrail: () => ({ mutate, isPending: false, isError: false }),
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const renderDialog = (guardrail) =>
  render(
    <EditGuardrailDialog
      open
      onClose={vi.fn()}
      guardrail={guardrail}
      gatewayId="gateway-1"
    />,
  );

const save = () =>
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

const savedConfig = () => mutate.mock.lastCall[0].config;

describe("EditGuardrailDialog stage", () => {
  beforeEach(() => {
    mutate.mockClear();
  });

  it("lets an external provider run before and after the LLM", async () => {
    renderDialog({
      name: "lakera-guard",
      action: "block",
      stage: "pre",
      config: {},
    });

    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Stage/ }));
    fireEvent.click(
      within(await screen.findByRole("listbox")).getByText(
        "Before and after LLM",
      ),
    );
    save();

    expect(savedConfig()).toMatchObject({
      name: "lakera-guard",
      stage: "both",
    });
  });

  it("starts a Future AGI eval at its saved stage", () => {
    renderDialog({
      name: "futureagi-eval",
      action: "warn",
      stage: "post",
      config: { eval_ids: ["76"] },
    });

    expect(screen.getByRole("combobox", { name: /Stage/ })).toHaveTextContent(
      "After LLM",
    );
    save();

    expect(savedConfig().stage).toBe("post");
  });

  it("shows a post-only guardrail's fixed stage and saves no stage", () => {
    renderDialog({ name: "hallucination-detection", stage: "pre" });

    const stageField = screen.getByLabelText("Stage");
    expect(stageField).toHaveValue("After LLM");
    expect(stageField).toBeDisabled();
    // Only the action select is left to change.
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
    save();

    // The gateway ignores a stage here, and saving must not push one the
    // org never chose.
    expect(savedConfig()).not.toHaveProperty("stage");
  });

  it("drops a stale stage from a built-in guardrail", () => {
    renderDialog({ name: "pii-detector", stage: "both" });

    expect(screen.getByLabelText("Stage")).toHaveValue("Before LLM");
    save();

    expect(savedConfig()).not.toHaveProperty("stage");
  });
});

describe("EditGuardrailDialog block after the LLM", () => {
  const warning = /Blocking after the LLM is not enforced yet/;

  it("warns once an after-LLM stage is picked for a blocking check", async () => {
    renderDialog({
      name: "lakera-guard",
      action: "block",
      stage: "pre",
      config: {},
    });
    expect(screen.queryByText(warning)).not.toBeInTheDocument();

    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Stage/ }));
    fireEvent.click(
      within(await screen.findByRole("listbox")).getByText("After LLM"),
    );

    expect(screen.getByText(warning)).toBeInTheDocument();
  });

  it("warns for a blocking guardrail that always runs after the LLM", () => {
    renderDialog({ name: "data-leakage-prevention", action: "block" });

    expect(screen.getByText(warning)).toBeInTheDocument();
  });

  it("does not warn when the check only warns", () => {
    renderDialog({ name: "lakera-guard", action: "warn", stage: "both" });

    expect(screen.queryByText(warning)).not.toBeInTheDocument();
  });
});
