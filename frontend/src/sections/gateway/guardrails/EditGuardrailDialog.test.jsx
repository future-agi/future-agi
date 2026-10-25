import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import EditGuardrailDialog from "./EditGuardrailDialog";

const { mutate } = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useUpdateGuardrail: () => ({ mutate, isPending: false, isError: false }),
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const renderDialog = (guardrail) =>
  render(
    <EditGuardrailDialog
      open
      onClose={() => {}}
      guardrail={guardrail}
      gatewayId="gateway-1"
    />,
  );

// The gateway never receives a rule's stage: an org's check runs where its
// type runs (only data leakage and hallucination checks read the answer). So
// the dialog offers no stage, which would promise a check on the answer.
describe("EditGuardrailDialog", () => {
  beforeEach(() => mutate.mockReset());

  it("offers no stage to choose", () => {
    renderDialog({ name: "pii-detector", action: "block", stage: "pre" });

    expect(screen.getByLabelText(/Action/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Stage/)).toBeNull();
    expect(screen.queryByText("post")).toBeNull();
    expect(screen.queryByText("both")).toBeNull();
  });

  it("saves a rule without adding a stage to it", () => {
    renderDialog({ name: "pii-detector", action: "block", enabled: true });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(mutate).toHaveBeenCalledTimes(1);
    const [{ name, config }] = mutate.mock.calls[0];
    expect(name).toBe("pii-detector");
    expect(config).toEqual({
      name: "pii-detector",
      action: "block",
      enabled: true,
      mode: "sync",
    });
  });
});
