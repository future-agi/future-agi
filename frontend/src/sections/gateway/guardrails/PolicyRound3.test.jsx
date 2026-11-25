import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import PoliciesTab from "./PoliciesTab";
import PolicyDialog from "./PolicyDialog";

vi.mock("../keys/hooks/useApiKeys", () => ({
  useApiKeys: () => ({ data: [] }),
}));
vi.mock("./hooks/useGuardrailPolicies", () => ({
  useGuardrailPolicies: () => ({
    data: [
      {
        id: "dormant",
        name: "Saved before upgrade",
        scope: "key",
        mode: "enforce",
        is_active: true,
        runs_on_gateway: false,
        applied_keys: ["one"],
      },
      {
        id: "running",
        name: "Saved from the tab",
        scope: "key",
        mode: "enforce",
        is_active: true,
        runs_on_gateway: true,
        applied_keys: ["one"],
      },
      {
        id: "inactive",
        name: "Switched off",
        scope: "key",
        mode: "enforce",
        is_active: false,
        runs_on_gateway: false,
        applied_keys: ["one"],
      },
    ],
    isLoading: false,
    isError: false,
  }),
  useCreateGuardrailPolicy: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateGuardrailPolicy: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteGuardrailPolicy: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

describe("Policies tab, round 3", () => {
  it("marks an active key policy the gateway does not run yet", () => {
    render(<PoliciesTab gatewayId="gateway" />);
    const dormant = screen.getByTestId("guardrail-policy-row-dormant");
    expect(
      within(dormant).getByText("Not running – save to start"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("guardrail-policy-row-running")).queryByText(
        /Not running/,
      ),
    ).not.toBeInTheDocument();
    // An inactive row does not run either way; its switch already says so.
    expect(
      within(screen.getByTestId("guardrail-policy-row-inactive")).queryByText(
        /Not running/,
      ),
    ).not.toBeInTheDocument();
  });

  it("says how Monitor and Enforce behave on speech streams", () => {
    render(<PolicyDialog open onClose={vi.fn()} gatewayId="gateway" />);
    const note = screen.getByTestId("policy-mode-speech-note");
    expect(note).toHaveTextContent(
      /Monitor policies\s+and Warn or Log checks don.t run/,
    );
    expect(note).toHaveTextContent(/skipped on speech-to-text streams/);
  });
});
