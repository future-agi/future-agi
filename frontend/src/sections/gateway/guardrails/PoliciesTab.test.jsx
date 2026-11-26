import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import PoliciesTab from "./PoliciesTab";

vi.mock("./hooks/useGuardrailPolicies", () => ({
  useGuardrailPolicies: () => ({
    data: [
      {
        id: "hidden",
        name: "__ui_guardrails__",
        scope: "global",
        mode: "enforce",
        is_active: true,
      },
      {
        id: "p1",
        name: "Staging policy",
        scope: "key",
        mode: "monitor",
        is_active: true,
        applied_keys: ["one", "two"],
        updated_at: "2026-10-01T00:00:00Z",
      },
      {
        id: "p2",
        name: "Everywhere",
        scope: "global",
        mode: "enforce",
        is_active: false,
        applied_keys: [],
      },
    ],
    isLoading: false,
    isError: false,
  }),
  useUpdateGuardrailPolicy: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteGuardrailPolicy: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("./PolicyDialog", () => ({ default: () => null }));

describe("PoliciesTab", () => {
  it("hides reserved policies and shows mode and key count", () => {
    render(<PoliciesTab gatewayId="gateway" />);
    expect(screen.queryByText("__ui_guardrails__")).not.toBeInTheDocument();
    expect(screen.getByText("Staging policy")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("Monitor")).toBeInTheDocument();
  });
});
