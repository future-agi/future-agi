import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import PoliciesTab from "./PoliciesTab";
import PolicyDialog from "./PolicyDialog";
import EditGuardrailDialog from "./EditGuardrailDialog";

const { create, update, updateGuardrail, policies } = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
  updateGuardrail: vi.fn(),
  policies: [
    {
      id: "key-policy",
      name: "Staging policy",
      scope: "key",
      mode: "monitor",
      is_active: true,
      applied_keys: ["dashboard-one"],
    },
    {
      id: "org-policy",
      name: "Everywhere",
      scope: "global",
      mode: "monitor",
      is_active: true,
      applied_keys: [],
    },
    {
      id: "project-policy",
      name: "Project only",
      scope: "project",
      mode: "enforce",
      is_active: true,
      applied_keys: [],
    },
  ],
}));
vi.mock("../keys/hooks/useApiKeys", () => ({
  useApiKeys: () => ({
    data: [
      {
        id: "dashboard-one",
        name: "Staging",
        keyPrefix: "sk-a",
        status: "active",
      },
    ],
  }),
}));
vi.mock("./hooks/useGuardrailPolicies", () => ({
  useGuardrailPolicies: () => ({
    data: policies,
    isLoading: false,
    isError: false,
  }),
  useCreateGuardrailPolicy: () => ({ mutateAsync: create, isPending: false }),
  useUpdateGuardrailPolicy: () => ({ mutateAsync: update, isPending: false }),
  useDeleteGuardrailPolicy: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useUpdateGuardrail: () => ({ mutate: updateGuardrail, isPending: false }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

beforeEach(() => {
  create.mockReset();
  update.mockReset();
  updateGuardrail.mockReset();
});

describe("Policies tab manages key policies only", () => {
  it("does not offer Org-wide scope, and explains where org-wide checks live", () => {
    render(<PolicyDialog open onClose={vi.fn()} gatewayId="gateway" />);
    expect(
      screen.queryByRole("radio", { name: "Org-wide" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(
        /Org-wide checks are set on the Overview and Rules tabs/,
      ),
    ).toBeInTheDocument();
  });

  it("does not list org-wide or project policies, so it never saves one", () => {
    render(<PoliciesTab gatewayId="gateway" />);
    expect(screen.getByText("Staging policy")).toBeInTheDocument();
    expect(screen.queryByText("Everywhere")).not.toBeInTheDocument();
    expect(screen.queryByText("Project only")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: "Active Everywhere" }),
    ).not.toBeInTheDocument();
  });

  it("saves a policy without the ID of a key that was deleted", async () => {
    update.mockResolvedValue({ gateway_synced: true });
    render(
      <PolicyDialog
        open
        onClose={vi.fn()}
        gatewayId="gateway"
        policy={{
          ...policies[0],
          applied_keys: ["dashboard-one", "deleted-key"],
          checks: [],
        }}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(
        expect.objectContaining({
          scope: "key",
          applied_keys: ["dashboard-one"],
        }),
      ),
    );
  });
});

describe("Edit guardrail threshold", () => {
  it("refuses a threshold outside 0 to 1", async () => {
    render(
      <EditGuardrailDialog
        open
        onClose={vi.fn()}
        gatewayId="gateway"
        guardrail={{ name: "pii-detection", action: "block" }}
      />,
    );
    const field = screen.getByRole("spinbutton", { name: "Threshold" });
    await userEvent.type(field, "50");
    expect(screen.getByText(/Enter a number from 0 to 1/)).toBeInTheDocument();
    const saveButton = screen.getByRole("button", { name: "Save" });
    expect(saveButton).toBeDisabled();
    await userEvent.clear(field);
    await userEvent.type(field, "0.5");
    expect(saveButton).toBeEnabled();
    await userEvent.click(saveButton);
    expect(updateGuardrail).toHaveBeenCalledWith(
      expect.objectContaining({
        config: expect.objectContaining({ threshold: 0.5 }),
      }),
      expect.anything(),
    );
  });

  it("shows and replaces a threshold stored as confidence_threshold", async () => {
    render(
      <EditGuardrailDialog
        open
        onClose={vi.fn()}
        gatewayId="gateway"
        guardrail={{
          name: "pii-detection",
          action: "block",
          confidence_threshold: 50,
        }}
      />,
    );
    const field = screen.getByRole("spinbutton", { name: "Threshold" });
    expect(field).toHaveValue(50);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.clear(field);
    await userEvent.type(field, "0.7");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const sent = updateGuardrail.mock.calls[0][0].config;
    expect(sent.threshold).toBe(0.7);
    expect(sent).not.toHaveProperty("confidence_threshold");
  });
});
