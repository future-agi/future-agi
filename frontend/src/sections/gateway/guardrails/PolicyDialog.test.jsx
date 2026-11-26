import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import PolicyDialog from "./PolicyDialog";

const { create, update, keys, snackbar } = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
  snackbar: vi.fn(),
  keys: [
    {
      id: "dashboard-one",
      name: "Staging",
      keyPrefix: "sk-test",
      status: "active",
      secret: "DO-NOT-DISPLAY-SECRET",
    },
    {
      id: "dashboard-two",
      name: "Old key",
      keyPrefix: "sk-old",
      status: "revoked",
    },
    {
      // GET /agentcc/api-keys/ returns prefixes that already end in "...".
      id: "dashboard-three",
      name: "Dotted",
      keyPrefix: "sk-agentcc-4...",
      status: "active",
    },
  ],
}));
vi.mock("../keys/hooks/useApiKeys", () => ({
  useApiKeys: () => ({ data: keys }),
}));
vi.mock("./hooks/useGuardrailPolicies", () => ({
  useCreateGuardrailPolicy: () => ({ mutateAsync: create, isPending: false }),
  useUpdateGuardrailPolicy: () => ({ mutateAsync: update, isPending: false }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: snackbar }));

beforeEach(() => {
  create.mockReset();
  update.mockReset();
  snackbar.mockReset();
});

describe("PolicyDialog", () => {
  it("lists names and prefixes but never secrets, disables revoked keys and saves dashboard IDs", async () => {
    create.mockResolvedValue({ gateway_synced: true });
    render(<PolicyDialog open onClose={vi.fn()} gatewayId="gateway" />);
    await userEvent.type(
      screen.getByRole("textbox", { name: "Name" }),
      "Rollout",
    );
    await userEvent.click(screen.getByRole("combobox", { name: "API keys" }));
    expect(screen.getByText(/Staging \(sk-test…\)/)).toBeInTheDocument();
    expect(
      screen.getByText(/Old key \(sk-old…\)/).closest("li"),
    ).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByText(/DO-NOT-DISPLAY-SECRET/)).not.toBeInTheDocument();
    expect(
      screen.getByText("Dotted (sk-agentcc-4…) · active"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByText(/Staging \(sk-test…\)/));
    await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          scope: "key",
          applied_keys: ["dashboard-one"],
        }),
      ),
    );
  });

  it("saves PII remediation inside check config without a stage field", async () => {
    create.mockResolvedValue({ gateway_synced: true });
    render(<PolicyDialog open onClose={vi.fn()} gatewayId="gateway" />);
    await userEvent.type(
      screen.getByRole("textbox", { name: "Name" }),
      "PII rollout",
    );
    await userEvent.click(screen.getByRole("combobox", { name: "API keys" }));
    await userEvent.click(screen.getByText(/Staging \(sk-test…\)/));
    await userEvent.click(
      screen.getByRole("checkbox", { name: "PII Detection" }),
    );
    await userEvent.click(
      screen.getByRole("combobox", { name: "PII Detection action" }),
    );
    await userEvent.click(screen.getByRole("option", { name: "Log" }));
    await userEvent.click(
      screen.getByRole("combobox", { name: "Remediation" }),
    );
    await userEvent.click(screen.getByRole("option", { name: "Redact" }));
    await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          checks: [
            expect.objectContaining({
              name: "pii-detection",
              action: "log",
              config: { remediation: "redact" },
            }),
          ],
        }),
      ),
    );
    expect(create.mock.calls[0][0].checks[0]).not.toHaveProperty("stage");
  });

  it("shows field errors returned by the API and surfaces gateway warnings", async () => {
    // The shape src/utils/axios.js rejects with: the 400 body spread flat,
    // field errors under `details`, `result` a one-line string.
    create.mockRejectedValueOnce({
      status: false,
      type: "validation_error",
      code: "invalid",
      result: "applied_keys: Unknown or unauthorized key IDs",
      attr: "applied_keys",
      details: { applied_keys: ["Unknown or unauthorized key IDs"] },
      statusCode: 400,
    });
    create.mockResolvedValueOnce({
      gateway_synced: false,
      gateway_warning: "Sync pending",
    });
    render(<PolicyDialog open onClose={vi.fn()} gatewayId="gateway" />);
    await userEvent.type(
      screen.getByRole("textbox", { name: "Name" }),
      "Rollout",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
    expect(
      screen.getByText("Select at least one active key."),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("combobox", { name: "API keys" }));
    await userEvent.click(screen.getByText(/Staging \(sk-test…\)/));
    await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
    // Inline under the key picker, not as a form-level message.
    expect(
      await screen.findByText("Unknown or unauthorized key IDs"),
    ).toHaveClass("MuiFormHelperText-root");
    await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() =>
      expect(snackbar).toHaveBeenCalledWith("Sync pending", {
        variant: "warning",
      }),
    );
  });
});
