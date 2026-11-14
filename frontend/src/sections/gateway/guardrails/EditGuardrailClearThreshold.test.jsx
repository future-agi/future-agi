import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import EditGuardrailDialog from "./EditGuardrailDialog";

const { updateGuardrail } = vi.hoisted(() => ({ updateGuardrail: vi.fn() }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useUpdateGuardrail: () => ({ mutate: updateGuardrail, isPending: false }),
}));

describe("Edit guardrail, cleared threshold", () => {
  it("does not resend a stored threshold when the optional field is cleared", async () => {
    render(
      <EditGuardrailDialog
        open
        onClose={vi.fn()}
        gatewayId="gateway"
        guardrail={{ name: "pii-detection", action: "block", threshold: 50 }}
      />,
    );
    const field = screen.getByRole("spinbutton", { name: "Threshold" });
    await userEvent.clear(field);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(updateGuardrail).toHaveBeenCalledTimes(1);
    const { config } = updateGuardrail.mock.calls[0][0];
    expect(config).not.toHaveProperty("threshold");
    expect(config).not.toHaveProperty("confidence_threshold");
    expect(config.action).toBe("block");
  });
});
