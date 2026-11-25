import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import EditGuardrailDialog from "./EditGuardrailDialog";

const { updateGuardrail } = vi.hoisted(() => ({ updateGuardrail: vi.fn() }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useUpdateGuardrail: () => ({ mutate: updateGuardrail, isPending: false }),
}));

const clearAndSave = async (guardrail) => {
  updateGuardrail.mockReset();
  render(
    <EditGuardrailDialog
      open
      onClose={vi.fn()}
      gatewayId="gateway"
      guardrail={guardrail}
    />,
  );
  await userEvent.clear(screen.getByRole("spinbutton", { name: "Threshold" }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(updateGuardrail).toHaveBeenCalledTimes(1);
  return updateGuardrail.mock.calls[0][0].config;
};

describe("Edit guardrail, cleared threshold", () => {
  it("keeps a valid stored threshold, which a rules-array update would otherwise drop", async () => {
    const config = await clearAndSave({
      name: "pii-detection",
      action: "block",
      threshold: 0.42,
    });
    expect(config.threshold).toBe(0.42);
    expect(config).not.toHaveProperty("confidence_threshold");
  });

  it("does not resend a stored threshold outside 0 to 1 when the field is cleared", async () => {
    const config = await clearAndSave({
      name: "pii-detection",
      action: "block",
      threshold: 50,
    });
    expect(config).not.toHaveProperty("threshold");
    expect(config).not.toHaveProperty("confidence_threshold");
    expect(config.action).toBe("block");
  });
});
