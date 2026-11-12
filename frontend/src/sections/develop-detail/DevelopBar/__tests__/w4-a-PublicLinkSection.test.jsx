// Share › Public link under the workspace policy (W4-6, A3; review fixes 1
// and 5): an admin always sees an active link with Turn off, also while the
// policy is off, and the expiry choices are the server's.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";

const state = vi.hoisted(() => ({
  policy: null,
  links: [],
  revoke: vi.fn(),
  create: vi.fn(),
  linksArgs: [],
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "ws1" }),
}));
vi.mock("src/api/develop/dataset-access", () => ({
  useSharingPolicyQuery: () => ({ data: state.policy }),
}));
vi.mock("src/api/develop/dataset-versions", () => ({
  useDatasetVersions: () => ({ data: [{ number: 6 }] }),
  latestOf: (v) => v?.[0] ?? null,
}));
vi.mock("src/api/shared-links", () => ({
  useGetSharedLinks: (type, id) => {
    state.linksArgs.push([type, id]);
    return { data: type ? state.links : undefined };
  },
  useCreateSharedLink: () => ({ mutate: state.create, isPending: false }),
  useDeleteSharedLink: () => ({ mutate: state.revoke, isPending: false }),
}));
vi.mock("src/sections/common/ux/CopyButton", () => ({
  default: () => <button type="button">Copy</button>,
}));

const { default: PublicLinkSection } = await import("../PublicLinkSection");

const policy = (over = {}) => ({
  workspace_id: "ws1",
  workspace_name: "Support Agents",
  public_links: true,
  max_days: 30,
  expiry_choices: [1, 7, 30],
  default_expiry_days: 7,
  can_edit: true,
  ...over,
});
const active = {
  id: "shl_a",
  token: "tok123",
  url_path: "/shared/tok123",
  expires_at: new Date(Date.now() + 5 * 86400_000).toISOString(),
  include: { rows: true },
  version: "frozen",
  version_number: 6,
  views: 2,
  last_opened_at: new Date(Date.now() - 60_000).toISOString(),
  revoked_at: null,
  state: "active",
  opens: true,
};

afterEach(() => {
  cleanup();
  state.revoke.mockReset();
  state.create.mockReset();
  state.linksArgs = [];
  state.links = [];
});

describe("PublicLinkSection", () => {
  it("policy off, admin: the pointer, and the active link stays with Turn off and says it doesn't open", () => {
    state.policy = policy({ public_links: false });
    state.links = [{ ...active, opens: false }];
    render(<PublicLinkSection datasetId="ds" />);
    // Links are read while the policy is off too.
    expect(state.linksArgs.at(-1)).toEqual(["dataset", "ds"]);
    expect(screen.getByTestId("public-link-policy-off").textContent).toBe(
      "Public links are off for Support Agents. Turn them on in Workspace settings › General",
    );
    expect(screen.getByRole("textbox", { name: "Public link" }).value).toMatch(
      /\/shared\/tok123$/,
    );
    expect(screen.getByTestId("public-link-line").textContent).toMatch(
      /^This link doesn't open while public links are off for Support Agents\. Turning them on reopens it\. Expires /,
    );
    expect(
      screen.queryByRole("button", { name: "Create public link" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    expect(state.revoke).toHaveBeenCalledWith("shl_a", expect.any(Object));
  });

  it("policy off, admin, no active link: only the pointer and earlier links, no Create", () => {
    state.policy = policy({ public_links: false });
    state.links = [
      {
        ...active,
        id: "shl_old",
        revoked_at: new Date().toISOString(),
        views: 0,
      },
    ];
    render(<PublicLinkSection datasetId="ds" />);
    expect(screen.getByTestId("public-link-policy-off")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Create public link" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Turn off" })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Earlier links (1)" }),
    ).toBeTruthy();
    expect(screen.getByText(/· not opened$/)).toBeTruthy();
  });

  it("policy off, non-admin: nothing at all; on: one line, no links read", () => {
    state.policy = policy({ public_links: false, can_edit: false });
    const { container } = render(<PublicLinkSection datasetId="ds" />);
    expect(container.textContent).toBe("");
    cleanup();
    state.policy = policy({ can_edit: false });
    render(<PublicLinkSection datasetId="ds" />);
    expect(screen.getByTestId("public-link-section").textContent).toContain(
      "Public links are on for this workspace; an admin creates them.",
    );
    expect(state.linksArgs.every(([type]) => type === null)).toBe(true);
  });

  it("the expiry choices and the default are the server's", () => {
    state.policy = policy({
      max_days: 5,
      expiry_choices: [1, 5],
      default_expiry_days: 5,
    });
    render(<PublicLinkSection datasetId="ds" />);
    fireEvent.click(screen.getByRole("button", { name: "Create public link" }));
    const select = screen.getByLabelText("Link expires in");
    expect(select.textContent).toBe("5 days");
    fireEvent.mouseDown(select);
    const options = within(screen.getByRole("listbox"))
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(options).toEqual(["1 day", "5 days"]);
    fireEvent.click(screen.getByRole("option", { name: "1 day" }));
    fireEvent.click(
      within(screen.getByTestId("public-link-form")).getByRole("button", {
        name: "Create",
      }),
    );
    expect(state.create.mock.calls[0][0]).toMatchObject({
      resource_type: "dataset",
      resource_id: "ds",
      expires_in_days: 1,
    });
  });

  it("policy on, admin, active link: the anyone line and Turn off", () => {
    state.policy = policy();
    state.links = [active];
    render(<PublicLinkSection datasetId="ds" />);
    expect(screen.queryByTestId("public-link-policy-off")).toBeNull();
    expect(screen.getByTestId("public-link-line").textContent).toMatch(
      /^Anyone with the link can see the rows of v6, without signing in\. Expires /,
    );
    expect(screen.getByRole("button", { name: "Turn off" })).toBeTruthy();
  });
});
