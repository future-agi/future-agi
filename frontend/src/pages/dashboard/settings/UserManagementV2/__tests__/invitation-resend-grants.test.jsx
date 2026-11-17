/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import AllActionForm from "../AllActionForm";

// Keep react-hook-form real: these cases exercise reset, workspace edits and
// handleSubmit. Only presentation, deployment, data loading and HTTP are mocked.
const fixture = vi.hoisted(() => ({
  isOSS: false,
  post: vi.fn().mockResolvedValue({ data: { result: {} } }),
  snackbar: vi.fn(),
  queryClient: { invalidateQueries: vi.fn() },
  workspaces: [
    { id: "00000000-0000-4000-8000-00000000000a", name: "Workspace A" },
    { id: "00000000-0000-4000-8000-00000000000b", name: "Workspace B" },
    { id: "00000000-0000-4000-8000-00000000000c", name: "Workspace C" },
  ],
}));

vi.mock("src/utils/axios", () => ({
  default: { post: fixture.post },
  endpoints: { rbac: { inviteResend: "/synthetic/invite/resend" }, workspace: {} },
}));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => fixture.queryClient,
  useMutation: ({ mutationFn }) => ({
    mutate: (data) => { void mutationFn(data); },
    isPending: false,
  }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: fixture.isOSS, isSuccess: true }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: fixture.workspaces }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: {}, orgLevel: 15 }),
}));
vi.mock("../UserManagementStore", () => ({
  useUserManagementStore: () => ({ usersList: [] }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: fixture.snackbar }));
vi.mock("src/utils/Mixpanel", () => ({ Events: {}, PropertyName: {}, trackEvent: vi.fn() }));
vi.mock("../invite-links", () => ({ InviteLinksResult: () => null, normalizeInvites: vi.fn() }));
vi.mock("src/components/ChipsInput/ChipsInput", () => ({ default: () => null }));
vi.mock("src/components/show", () => ({
  ShowComponent: ({ condition, children }) => condition ? children : null,
}));
vi.mock("../ActionForm", () => ({
  default: ({ formSection, actionButton }) => <div>{formSection}{actionButton}</div>,
}));
vi.mock("src/components/FromSearchSelectField", async () => {
  const { Controller } = await import("react-hook-form");
  return {
    FormSearchSelectFieldControl: ({ control, fieldName, label, options, disabled }) => (
      <Controller control={control} name={fieldName} render={({ field, fieldState }) => (
        <label>
          {label}
          <select {...field} aria-label={label} disabled={disabled}
            value={field.value ?? ""} onChange={(event) => field.onChange(Number(event.target.value))}>
            <option value="" />
            {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          {fieldState.error && <span role="alert">{fieldState.error.message}</span>}
        </label>
      )} />
    ),
  };
});
vi.mock("@mui/material", () => ({
  alpha: (color) => color,
  useTheme: () => ({ palette: { primary: { main: "blue", dark: "navy" } } }),
  Box: ({ children }) => <div>{children}</div>,
  Typography: ({ children }) => <p>{children}</p>,
  Button: ({ children, onClick }) => <button onClick={onClick}>{children}</button>,
  Chip: ({ label }) => <span>{label}</span>,
  TextField: ({ helperText }) => helperText ? <span role="alert">{helperText}</span> : null,
  Autocomplete: ({ options, value, onChange, getOptionLabel, renderInput }) => {
    const input = renderInput({});
    return <div>
      <select multiple aria-label={input.props.label} value={value.map((option) => option.id)}
        onChange={(event) => onChange(event, Array.from(event.target.selectedOptions,
          (selected) => options.find((option) => option.id === selected.value)))}>
        {options.map((option) => <option key={option.id} value={option.id}>{getOptionLabel(option)}</option>)}
      </select>
      {input}
    </div>;
  },
}));
vi.mock("@mui/lab", () => ({
  LoadingButton: ({ children, onClick, disabled }) => <button onClick={onClick} disabled={disabled}>{children}</button>,
}));

const [A, B, C] = fixture.workspaces.map((workspace) => workspace.id);
const storedGrants = [
  { workspace_id: A, level: 8 },
  { workspace_id: B, level: 1 },
];
const invitation = {
  id: "00000000-0000-4000-8000-000000000001",
  type: "invite",
  status: "Pending",
  org_level: 1,
  ws_level: 8, // Summary only: it must not replace B's actual Viewer role.
  workspaces: storedGrants.map((grant, index) => ({
    workspace_id: grant.workspace_id,
    workspace_name: fixture.workspaces[index].name,
    ws_level: grant.level,
  })),
};

function mount(userData = invitation, workspaceId) {
  return render(<AllActionForm openActionForm={{ action: "resend-invite" }}
    onClose={vi.fn()} userData={userData} workspaceId={workspaceId} />);
}

async function clickSend() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: fixture.isOSS ? "Renew invitation" : "Send" }));
  });
}

async function submittedPayload() {
  await clickSend();
  await waitFor(() => expect(fixture.post).toHaveBeenCalledTimes(1));
  expect(fixture.post.mock.calls[0][0]).toBe("/synthetic/invite/resend");
  return fixture.post.mock.calls[0][1];
}

function selectWorkspaces(ids) {
  const select = screen.getByLabelText("Workspaces");
  for (const option of select.options) option.selected = ids.includes(option.value);
  fireEvent.change(select);
}

// This is the read backend contract, not a backend integration assertion:
// InviteResendAPIView retains stored grants iff workspace_access is absent.
function effectiveGrants(payload) {
  return Object.hasOwn(payload, "workspace_access") ? payload.workspace_access : storedGrants;
}

beforeEach(() => {
  vi.clearAllMocks();
  fixture.isOSS = false;
});
afterEach(cleanup);

describe("Invitation resend workspace grants", () => {
  it.each([false, true])("keeps heterogeneous grants on unedited resend (OSS=%s)", async (isOSS) => {
    fixture.isOSS = isOSS;
    mount();
    const payload = await submittedPayload();
    expect(payload).not.toHaveProperty("workspace_access");
    expect(effectiveGrants(payload)).toEqual(storedGrants);
    expect(payload).toMatchObject({ invite_id: invitation.id, org_level: 1 });
    if (isOSS) expect(payload.send_email).toBe(false);
    else expect(payload).not.toHaveProperty("send_email");
  });

  it.each([false, true])("resends a workspace-member projection without refusing or dropping grants (OSS=%s)", async (isOSS) => {
    fixture.isOSS = isOSS;
    const { workspaces, ...workspaceRow } = invitation;
    mount(workspaceRow, A);
    const payload = await submittedPayload();
    expect(screen.queryByText("Please select at least one workspace")).toBeNull();
    expect(payload).not.toHaveProperty("workspace_access");
    expect(effectiveGrants(payload)).toEqual(storedGrants);
    // A partial row must not offer a replacement selector that can drop B.
    expect(screen.queryByLabelText("Workspaces")).toBeNull();
    if (isOSS) expect(payload.send_email).toBe(false);
    else expect(payload).not.toHaveProperty("send_email");
  });

  it("initializes individual roles and changes only the edited workspace", async () => {
    mount();
    expect(screen.getByLabelText("Workspace A role").value).toBe("8");
    expect(screen.getByLabelText("Workspace B role").value).toBe("1");
    fireEvent.change(screen.getByLabelText("Workspace B role"), { target: { value: "3" } });
    const payload = await submittedPayload();
    expect(payload.workspace_access).toEqual([
      { workspace_id: A, level: 8 },
      { workspace_id: B, level: 3 },
    ]);
  });

  it("adds a workspace without flattening existing roles", async () => {
    mount();
    selectWorkspaces([A, B, C]);
    const payload = await submittedPayload();
    expect(payload.workspace_access).toEqual([
      ...storedGrants,
      { workspace_id: C, level: 3 },
    ]);
  });

  it("removes only the deselected workspace and keeps the remaining role", async () => {
    mount();
    selectWorkspaces([B]);
    const payload = await submittedPayload();
    expect(payload.workspace_access).toEqual([{ workspace_id: B, level: 1 }]);
  });

  it("keeps grants when only the organization role changes", async () => {
    mount();
    fireEvent.change(screen.getByLabelText("Organization role"), { target: { value: "3" } });
    const payload = await submittedPayload();
    expect(payload.org_level).toBe(3);
    expect(payload).not.toHaveProperty("workspace_access");
    expect(effectiveGrants(payload)).toEqual(storedGrants);
  });

  it("omits workspace replacement after a workspace role edit is reverted", async () => {
    mount();
    const role = screen.getByLabelText("Workspace B role");
    fireEvent.change(role, { target: { value: "3" } });
    fireEvent.change(role, { target: { value: "1" } });
    const payload = await submittedPayload();
    expect(payload).not.toHaveProperty("workspace_access");
  });

  it("still requires an explicit workspace when demoting an Admin invitation", async () => {
    mount({ ...invitation, org_level: 8, workspaces: [] });
    fireEvent.change(screen.getByLabelText("Organization role"), { target: { value: "1" } });
    await clickSend();
    expect(fixture.post).not.toHaveBeenCalled();
    expect(screen.getByText("Please select at least one workspace")).toBeTruthy();
    selectWorkspaces([B]);
    const payload = await submittedPayload();
    expect(payload.org_level).toBe(1);
    expect(payload.workspace_access).toEqual([{ workspace_id: B, level: 3 }]);
  });

  it("does not replace unknown grants to demote a workspace-projected Admin invitation", async () => {
    const { workspaces, ...workspaceRow } = invitation;
    mount({ ...workspaceRow, org_level: 8 }, A);
    fireEvent.change(screen.getByLabelText("Organization role"), { target: { value: "1" } });
    await clickSend();
    expect(fixture.post).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Workspaces")).toBeNull();
    expect(screen.getByRole("alert").textContent).toMatch(/Organization Members/);
  });
});

// The dialogs name the role fields the way the Members table names its
// columns: "Organization role" and "Workspace role", in sentence case.
describe("Role field labels in the member dialogs", () => {
  it.each([
    ["invite-user", undefined, ["Organization role", "Workspace role"]],
    ["edit-role", { ...invitation, type: "member", status: "Active" }, ["Organization role"]],
    ["resend-invite", invitation, ["Organization role"]],
    ["edit-ws-role", { ...invitation, type: "member", status: "Active" }, ["Workspace role"]],
  ])("labels the %s fields in sentence case", (action, userData, expected) => {
    render(<AllActionForm openActionForm={{ action }} onClose={vi.fn()}
      userData={userData} workspaceId={A} />);
    expected.forEach((name) => {
      expect(screen.getByRole("combobox", { name })).toBeInTheDocument();
    });
    ["Organization Role", "Workspace Role", "Organisation Role"].forEach((name) => {
      expect(screen.queryByRole("combobox", { name })).toBeNull();
    });
  });
});
