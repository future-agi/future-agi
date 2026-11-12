import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SelectOrganization from "./SelectOrganization";

const h = vi.hoisted(() => ({ query: {}, refetch: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => h.query,
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() }, endpoints: { organizations: { list: "/organizations" } },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "a", switchOrganization: vi.fn() }),
}));
vi.mock("src/utils/Mixpanel", () => ({ Events: {}, trackEvent: vi.fn() }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/prototype", () => ({ isPrototypeMode: () => false }));

function mount({ canCreateOrganization = true } = {}) {
  return render(
    <SelectOrganization
      open
      anchorEl={document.body}
      setOpen={vi.fn()}
      canCreateOrganization={canCreateOrganization}
      ref={{ current: null }}
    />,
  );
}

beforeEach(() => {
  h.refetch.mockReset();
  h.query = { data: undefined, isPending: false, isError: true, isFetching: false, refetch: h.refetch };
});
afterEach(cleanup);

describe("R3-SHELL-DX-02 organization list recovery", () => {
  it("shows a local error and retries a cold failed read", async () => {
    mount();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load organizations");
    expect(screen.queryByText("No organizations available")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(h.refetch).toHaveBeenCalledTimes(1);
  });

  it("retains cached choices, labels them stale and disables a pending retry", () => {
    h.query = { ...h.query, data: { data: { result: { organizations: [{ id: "a", name: "Acme" }] } } }, isFetching: true };
    mount();
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not refresh organizations. Showing the last loaded list.");
    expect(screen.getByRole("button", { name: "Retrying…" })).toBeDisabled();
  });

  it("distinguishes a successful empty result from a failed read", () => {
    h.query = { ...h.query, isError: false, data: { data: { organizations: [] } } };
    mount();
    expect(screen.getByText("No organizations available")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create Organization" })).toBeInTheDocument();
  });
});

describe("J-AUTH-WSL-002 organization sub-menu create control", () => {
  it.each([false, true])(
    "keeps Create Organization visible and enables it only when can_create_organization=%s",
    (allowed) => {
      h.query = { ...h.query, isError: false, data: { data: { organizations: [] } } };
      mount({ canCreateOrganization: allowed });
      const button = screen.getByRole("button", { name: "Create Organization" });
      expect(button.hasAttribute("disabled")).toBe(!allowed);
      expect(
        Boolean(
          screen.queryByText(
            "Creating organizations isn't available in single sign-on sessions.",
          ),
        ),
      ).toBe(!allowed);
    },
  );
});
