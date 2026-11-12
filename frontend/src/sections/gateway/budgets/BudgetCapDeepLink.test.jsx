import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithRouter } from "src/utils/test-utils";
import BudgetCapDeepLink from "./BudgetCapDeepLink";

const mocks = vi.hoisted(() => ({
  canEdit: true,
  org: "org-a",
  read: vi.fn(),
  write: vi.fn(),
  refetch: vi.fn(),
  error: null,
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    currentOrganizationId: mocks.org,
    currentOrganizationName: mocks.org,
  }),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: mocks.canEdit }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default" }),
}));
vi.mock("./hooks/useBudgetCapEditor", () => ({
  useBudgetCapEditor: (args) => {
    mocks.read(args);
    return {
      query: {
        data: {
          budgets: {
            enabled: true,
            policy_revision: 3,
            org_limit: { limit: 5, on_exceed: "block" },
          },
        },
        dataUpdatedAt: 1,
        refetch: mocks.refetch,
      },
      mutation: { mutate: mocks.write, error: mocks.error },
    };
  },
}));

function open(search = "level=org&id=current&org=org-a") {
  return renderWithRouter(<BudgetCapDeepLink />, {
    route: `/dashboard/gateway/budgets?${search}`,
  });
}

beforeEach(() => {
  mocks.canEdit = true;
  mocks.org = "org-a";
  mocks.error = null;
  vi.clearAllMocks();
});

describe("focused cap editor", () => {
  it("does not load another API on an ordinary Budgets visit", () => {
    open("");
    expect(mocks.read).not.toHaveBeenCalled();
  });
  it("opens the right cap with focus on its USD limit and saves only on click", async () => {
    open();
    const input = await screen.findByLabelText("New limit (USD)");
    await waitFor(() => expect(document.activeElement).toBe(input));
    expect(mocks.write).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "8" } });
    fireEvent.click(screen.getByRole("button", { name: "Raise cap" }));
    expect(mocks.write).toHaveBeenCalledWith(
      { budgets: { policy_revision: 3, org_limit: { limit: 8 } } },
      expect.any(Object),
    );
  });
  it("does not load or expose a writer to a member", () => {
    mocks.canEdit = false;
    open();
    screen.getByText(/Only an organization Owner or Admin/);
    expect(mocks.read).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Raise cap" })).toBeNull();
  });
  it("rejects a link naming another organization without making a request", () => {
    open("level=org&id=current&org=org-b");
    screen.getByText(/Switch to the organization/);
    expect(mocks.read).not.toHaveBeenCalled();
  });
  it("clears editor query parameters on close, preserving unrelated parameters", async () => {
    open("level=org&id=current&org=org-a&tab=limits");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(window.location.search).toBe("?tab=limits");
  });
  it("keeps a failed or stale write visible and offers refresh, not blind retry", () => {
    mocks.error = new Error("cap_policy_revision_conflict");
    open();
    screen.getByText(/cap_policy_revision_conflict/);
    fireEvent.click(screen.getByRole("button", { name: "Reload cap" }));
    expect(mocks.refetch).toHaveBeenCalledOnce();
    expect(mocks.write).not.toHaveBeenCalled();
  });
  it("never renders a save button for a live-run ledger", () => {
    open("level=agent_run&id=run%3Ascope-1");
    screen.getByText(/cannot raise an existing run/i);
    expect(screen.queryByRole("button", { name: "Raise cap" })).toBeNull();
  });
});
