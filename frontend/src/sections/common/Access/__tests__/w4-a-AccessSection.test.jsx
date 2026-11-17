// Dataset settings › Access (W4-5, A1): picker, request, read-only mode.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";

const state = vi.hoisted(() => ({
  view: null,
  put: vi.fn(),
  post: vi.fn(),
  members: [],
}));
vi.mock("src/api/develop/dataset-access", () => ({
  useDatasetAccessQuery: () => ({ data: state.view, isLoading: false }),
  useUpdateDatasetAccess: () => ({ mutate: state.put, isPending: false }),
  useRequestDatasetAccess: () => ({ mutate: state.post, isPending: false }),
}));
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useOrgMembers: () => ({ data: state.members }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-1" }),
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));

const { default: AccessSection } = await import("../AccessSection");
const { enqueueSnackbar } = await import("src/components/snackbar");

const LEVELS = [
  {
    key: "edit_run",
    label: "Edit and run",
    perms: ["view", "download", "edit", "run"],
  },
  { key: "edit", label: "Edit", perms: ["view", "download", "edit"] },
  {
    key: "view_download",
    label: "View and download",
    perms: ["view", "download"],
  },
  { key: "view", label: "View", perms: ["view"] },
  { key: "none", label: "No access", perms: [] },
];
const ALICE = {
  id: "u-alice",
  name: "Alice Park",
  email: "alice.park@acme.ai",
};
const ASHA = { id: "u-asha", name: "Asha Rao", email: "asha.rao@acme.ai" };
const adminView = (over = {}) => ({
  dataset_id: "ds",
  dataset_name: "Golden",
  workspace: { id: "w", name: "Support Agents" },
  workspace_level: "edit_run",
  levels: LEVELS,
  grants: [],
  requests: [],
  me: {
    user_id: "me",
    name: "Maya Chen",
    role: "admin",
    perms: { view: true, download: true, edit: true, run: true, manage: true },
    via: "admin",
  },
  can_manage: true,
  ask: [ASHA],
  ...over,
});

beforeEach(() => {
  state.put.mockReset();
  state.post.mockReset();
  state.members = [ALICE, ASHA];
});
afterEach(cleanup);

describe("AccessSection", () => {
  it("an admin changes the workspace level; the PUT carries it and the toast names it", () => {
    state.view = adminView();
    state.put.mockImplementation((body, { onSuccess }) => onSuccess());
    render(<AccessSection datasetId="ds" />);
    expect(screen.getByText("Everyone in Support Agents can")).toBeTruthy();
    expect(
      screen.getByText(
        "Workspace admins always have full access. Viewers get download only when you grant it.",
      ),
    ).toBeTruthy();
    fireEvent.mouseDown(
      screen
        .getByLabelText("Access for everyone in Support Agents")
        .parentElement.querySelector('[role="combobox"]') ??
        screen.getByLabelText("Access for everyone in Support Agents"),
    );
    fireEvent.click(within(screen.getByRole("listbox")).getByText("View"));
    expect(state.put).toHaveBeenCalledWith(
      { workspace_level: "view" },
      expect.any(Object),
    );
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Everyone in Support Agents can now view",
      { variant: "success" },
    );
  });

  it("+ Add people lists org members without a grant and adds View", () => {
    state.view = adminView({
      grants: [
        { user: ASHA, level: "edit_run", granted_by: {}, granted_at: "" },
      ],
    });
    render(<AccessSection datasetId="ds" />);
    fireEvent.click(screen.getByText("Add people"));
    const input = screen.getByPlaceholderText(
      "Search people in this organization",
    );
    fireEvent.focus(input);
    fireEvent.keyDown(input, { key: "ArrowDown" });
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Alice Park · alice.park@acme.ai"]);
    fireEvent.click(screen.getByRole("option"));
    expect(state.put).toHaveBeenCalledWith(
      { grants: [{ user_id: "u-alice", level: "view" }] },
      expect.any(Object),
    );
  });

  it("restricted note and the admin's request list: Grant raises to the level that adds it", () => {
    state.view = adminView({
      workspace_level: "none",
      grants: [{ user: ALICE, level: "view", granted_by: {}, granted_at: "" }],
      requests: [
        {
          id: "r1",
          user: ALICE,
          perm: "download",
          at: new Date().toISOString(),
        },
      ],
    });
    render(<AccessSection datasetId="ds" />);
    expect(
      screen.getByText(
        "Restricted · only the people below and workspace admins can open this dataset.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(/Alice Park asked for download access/),
    ).toBeTruthy();
    fireEvent.click(screen.getByText("Grant"));
    expect(state.put).toHaveBeenCalledWith(
      { grants: [{ user_id: "u-alice", level: "view_download" }] },
      expect.any(Object),
    );
    fireEvent.click(screen.getByText("Dismiss"));
    expect(state.put).toHaveBeenLastCalledWith(
      { dismiss_requests: ["r1"] },
      expect.any(Object),
    );
  });

  it("a non-admin reads it, with their own line and a request button", () => {
    state.view = adminView({
      workspace_level: "view",
      can_manage: false,
      me: {
        user_id: "me",
        name: "Maya Chen",
        role: "member",
        perms: {
          view: true,
          download: false,
          edit: false,
          run: false,
          manage: false,
        },
        via: "workspace",
        level: "view",
      },
      ask: [ASHA, { id: "u-ravi", name: "Ravi Menon" }],
    });
    render(<AccessSection datasetId="ds" />);
    expect(
      screen.queryByLabelText("Access for everyone in Support Agents"),
    ).toBeNull();
    expect(screen.queryByText("Add people")).toBeNull();
    expect(
      screen.getByText(
        "You can view this dataset. Ask Asha Rao or Ravi Menon for more.",
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByText("Request download access"));
    expect(state.post).toHaveBeenCalledWith("download", expect.any(Object));
  });

  it("an open request of the caller reads Requested", () => {
    state.view = adminView({
      can_manage: false,
      me: {
        user_id: "me",
        name: "Maya Chen",
        role: "member",
        perms: {
          view: true,
          download: false,
          edit: false,
          run: false,
          manage: false,
        },
        via: "workspace",
        level: "view",
      },
      requests: [
        {
          id: "r1",
          user: { id: "me", name: "Maya Chen" },
          perm: "download",
          at: "",
        },
      ],
    });
    render(<AccessSection datasetId="ds" />);
    expect(
      screen.getByText("Requested · an admin sees it under Access"),
    ).toBeTruthy();
    expect(screen.queryByText("Request download access")).toBeNull();
  });
});
