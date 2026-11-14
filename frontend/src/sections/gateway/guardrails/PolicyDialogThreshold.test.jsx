import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import PolicyDialog from "./PolicyDialog";

const { update, seen } = vi.hoisted(() => ({ update: vi.fn(), seen: {} }));
vi.mock("../keys/hooks/useApiKeys", () => ({
  useApiKeys: () => ({
    data: [{ id: "k1", name: "Staging", keyPrefix: "sk-t", status: "active" }],
  }),
}));
vi.mock("./hooks/useGuardrailPolicies", () => ({
  useCreateGuardrailPolicy: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateGuardrailPolicy: () => ({ mutateAsync: update, isPending: false }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
// Stands in for the check's Configure dialog: records what it was opened with
// and saves a new confidence threshold, as its slider does.
vi.mock("../settings/GuardrailCheckDialog", () => ({
  default: ({ initialData, onSave, checkName }) => {
    seen.initialData = initialData;
    return (
      <button
        type="button"
        onClick={() =>
          onSave(checkName, {
            ...initialData,
            action: "block",
            confidence_threshold: 0.9,
          })
        }
      >
        Save threshold
      </button>
    );
  },
}));

describe("PolicyDialog thresholds", () => {
  it("edits a check saved with `threshold` and submits the new value as `threshold`", async () => {
    update.mockResolvedValue({ gateway_synced: true });
    const policy = {
      id: "p1",
      name: "Blocklist rollout",
      scope: "key",
      mode: "monitor",
      applied_keys: ["k1"],
      is_active: true,
      checks: [
        {
          name: "keyword-blocklist",
          enabled: true,
          action: "block",
          threshold: 0.2,
        },
      ],
    };
    render(<PolicyDialog open onClose={vi.fn()} policy={policy} />);
    await userEvent.click(screen.getByRole("button", { name: "Configure" }));
    expect(seen.initialData.confidence_threshold).toBe(0.2);
    await userEvent.click(
      // Rendered beside the modal, which hides siblings from the a11y tree.
      screen.getByText("Save threshold"),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(update).toHaveBeenCalled());
    const [check] = update.mock.calls[0][0].checks;
    expect(check.threshold).toBe(0.9);
    // What axios sends: undefined fields are dropped from the JSON body.
    expect(JSON.parse(JSON.stringify(check))).not.toHaveProperty(
      "confidence_threshold",
    );
  });
});
