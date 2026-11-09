// Dataset settings › Access states each change's result on its own line, so
// it is seen even when the app hides toasts that would cross the dialog.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";

const state = vi.hoisted(() => ({ view: null, put: vi.fn() }));
vi.mock("src/api/develop/dataset-access", () => ({
  useDatasetAccessQuery: () => ({ data: state.view, isLoading: false }),
  useUpdateDatasetAccess: () => ({ mutate: state.put, isPending: false }),
  useRequestDatasetAccess: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useOrgMembers: () => ({ data: [] }),
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
  { key: "view", label: "View", perms: ["view"] },
  { key: "none", label: "No access", perms: [] },
];
const ASHA = { id: "u-asha", name: "Asha Rao", email: "asha.rao@acme.ai" };
const view = {
  dataset_id: "ds",
  dataset_name: "Golden",
  workspace: { id: "w", name: "Support Agents" },
  workspace_level: "view",
  levels: LEVELS,
  grants: [{ user: ASHA, level: "view", granted_by: {}, granted_at: "" }],
  requests: [],
  me: {
    user_id: "me",
    name: "Maya Chen",
    role: "admin",
    perms: { view: true, download: true, edit: true, run: true, manage: true },
    via: "admin",
  },
  can_manage: true,
  ask: [],
};

const pick = (aria, label) => {
  const el = screen.getByLabelText(aria);
  fireEvent.mouseDown(
    el.parentElement.querySelector('[role="combobox"]') ?? el,
  );
  fireEvent.click(within(screen.getByRole("listbox")).getByText(label));
};

beforeEach(() => {
  state.view = view;
  state.put.mockReset();
  enqueueSnackbar.mockClear();
});
afterEach(cleanup);

describe("Access status line", () => {
  it("a grant change says its result on the line and in the toast", () => {
    state.put.mockImplementation((body, { onSuccess }) => onSuccess());
    render(<AccessSection datasetId="ds" />);
    expect(screen.queryByTestId("dataset-access-status")).toBeNull();
    pick("Access for Asha Rao", "Edit and run");
    const line = screen.getByTestId("dataset-access-status");
    expect(line.getAttribute("role")).toBe("status");
    expect(line.textContent).toBe("Asha Rao can now edit and run");
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Asha Rao can now edit and run",
      { variant: "success" },
    );
  });

  it("a refused change shows the server's words as an alert", () => {
    state.put.mockImplementation((body, { onError }) =>
      onError({ error: "Only members of this workspace can be given access." }),
    );
    render(<AccessSection datasetId="ds" />);
    pick("Access for everyone in Support Agents", "No access");
    const line = screen.getByTestId("dataset-access-status");
    expect(line.getAttribute("role")).toBe("alert");
    expect(line.textContent).toBe(
      "Only members of this workspace can be given access.",
    );
  });
});
