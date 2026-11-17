/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import AllActionForm from "../AllActionForm";
import { LEVELS } from "../constant";

// The actor's role comes from useAuthContext, as in members-remove-actions and
// members-scope-actions. Rendering mocks follow invitation-resend-grants, the
// suite that drives AllActionForm itself; react-hook-form stays real.
const fixture = vi.hoisted(() => ({
  auth: {},
  post: vi.fn(),
  del: vi.fn(),
  snackbar: vi.fn(),
  queryClient: { invalidateQueries: vi.fn() },
  memberRemoveUrl: "/synthetic/rbac/member/remove",
  workspaceMemberRemoveUrl: (id) =>
    `/synthetic/rbac/workspace/${id}/member/remove`,
}));

vi.mock("src/utils/axios", () => ({
  default: { post: fixture.post, delete: fixture.del },
  endpoints: {
    rbac: {
      memberRemove: fixture.memberRemoveUrl,
      workspaceMemberRemove: fixture.workspaceMemberRemoveUrl,
    },
    workspace: {},
  },
}));
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => fixture.queryClient,
  // Settles like the real hook: later, through onSuccess or onError.
  useMutation: ({ mutationFn, onSuccess, onError }) => ({
    mutate: (variables) => {
      Promise.resolve()
        .then(() => mutationFn(variables))
        .then(
          (response) => onSuccess?.(response, variables),
          (error) => onError?.(error, variables),
        );
    },
    isPending: false,
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => fixture.auth,
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false, isSuccess: true }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
}));
vi.mock("../UserManagementStore", () => ({
  useUserManagementStore: () => ({ usersList: [] }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: fixture.snackbar }));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("../invite-links", () => ({
  InviteLinksResult: () => null,
  normalizeInvites: vi.fn(),
}));
vi.mock("src/components/ChipsInput/ChipsInput", () => ({
  default: () => null,
}));
vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldControl: () => null,
}));
vi.mock("src/components/show", () => ({
  ShowComponent: ({ condition, children }) => (condition ? children : null),
}));
vi.mock("../ActionForm", () => ({
  default: ({ open, onClose, title, formSection, actionButton }) =>
    open ? (
      <div role="dialog">
        <h2>{title}</h2>
        {formSection}
        {actionButton}
        <button type="button" aria-label="Dismiss dialog" onClick={onClose} />
      </div>
    ) : null,
}));
vi.mock("@mui/material", () => ({
  alpha: (color) => color,
  useTheme: () => ({ palette: { primary: { main: "blue", dark: "navy" } } }),
  Box: ({ children }) => <div>{children}</div>,
  Typography: ({ children }) => <p>{children}</p>,
  Button: ({ children, onClick }) => (
    <button onClick={onClick}>{children}</button>
  ),
  Chip: ({ label }) => <span>{label}</span>,
  TextField: () => null,
  Autocomplete: () => null,
}));
vi.mock("@mui/lab", () => ({
  LoadingButton: ({ children, onClick, disabled }) => (
    <button onClick={onClick} disabled={disabled}>
      {children}
    </button>
  ),
}));

const WORKSPACE_ID = "00000000-0000-4000-8000-0000000000a1";
const WORKSPACE_URL = fixture.workspaceMemberRemoveUrl(WORKSPACE_ID);
const member = {
  id: "00000000-0000-4000-8000-000000000101",
  name: "Sam Rivera",
  email: "sam.rivera@example.test",
  status: "Active",
  org_level: LEVELS.MEMBER,
  ws_level: LEVELS.WORKSPACE_MEMBER,
};

const ONLY_WORKSPACE_NOTICE =
  "This is Sam Rivera's only workspace, so only an organization Admin or Owner can remove them.";
const WORKSPACE_QUESTION =
  "Are you sure you want to remove this member from the workspace?";
const ORG_REMOVE_QUESTION =
  "This is the user's only workspace. Removing them will also remove them from the organization entirely.";

// Backend replies, as the axios interceptor rejects them (flattened body).
// CANNOT_REMOVE_LAST_WS in tfc/utils/error_codes.py; IsOrganizationAdmin in tfc/permissions/rbac.py.
const LAST_WORKSPACE_ERROR = {
  result:
    "Cannot remove member from their only workspace. To remove them entirely, use 'Remove from organization' instead.",
};
const ORG_ADMIN_REQUIRED = { result: "Organization admin access required." };

// The backend decides: a last workspace is refused, and org removal needs an org Admin or Owner.
function serve({ hasOtherWorkspaces = false, actorIsOrgAdmin = false } = {}) {
  fixture.del.mockImplementation((url) => {
    if (url === WORKSPACE_URL) {
      return hasOtherWorkspaces
        ? Promise.resolve({ data: { result: {} } })
        : Promise.reject(LAST_WORKSPACE_ERROR);
    }
    if (url === fixture.memberRemoveUrl) {
      return actorIsOrgAdmin
        ? Promise.resolve({ data: { result: {} } })
        : Promise.reject(ORG_ADMIN_REQUIRED);
    }
    return Promise.reject({ result: `unexpected request ${url}` });
  });
}

// Workspace Members' remove flow. Stays mounted when closed, like the page-level
// mounts in UserManagementV2, so leftover dialog state would show up here.
function renderRemoval() {
  const onClose = vi.fn();
  function MembersPage() {
    const [openActionForm, setOpenActionForm] = React.useState({
      action: "remove-ws-member",
    });
    return (
      <>
        <AllActionForm
          openActionForm={openActionForm}
          onClose={() => {
            onClose();
            setOpenActionForm(null);
          }}
          userData={member}
          workspaceId={WORKSPACE_ID}
        />
        {!openActionForm && (
          <button
            type="button"
            onClick={() => setOpenActionForm({ action: "remove-ws-member" })}
          >
            Remove from workspace
          </button>
        )}
      </>
    );
  }
  render(<MembersPage />);
  return { onClose };
}

async function click(name) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
}

const requestsTo = (url) =>
  fixture.del.mock.calls.filter(([calledUrl]) => calledUrl === url);

describe("Workspace Members: removing a member whose only workspace this is", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fixture.auth = {
      user: {
        id: "actor-1",
        email: "actor@example.test",
        organization_role: "Member",
      },
      orgLevel: LEVELS.MEMBER,
      wsLevel: LEVELS.WORKSPACE_ADMIN,
      effectiveLevel: LEVELS.WORKSPACE_ADMIN,
    };
    serve();
  });

  it("W01: a workspace-only admin sees Remove from organization disabled, with the notice as its reason, and Close", async () => {
    const { onClose } = renderRemoval();
    expect(screen.getByText(WORKSPACE_QUESTION)).toBeVisible();

    await click("Remove");

    expect(await screen.findByText(ONLY_WORKSPACE_NOTICE)).toBeVisible();
    expect(screen.getByRole("button", { name: "Close" })).toBeVisible();
    const removeFromOrg = screen.getByRole("button", {
      name: "Remove from organization",
    });
    expect(removeFromOrg).toBeVisible();
    expect(removeFromOrg).toBeDisabled();
    await act(async () => {
      fireEvent.click(removeFromOrg);
    });
    for (const name of ["Remove", "Cancel"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    expect(screen.queryByText(ORG_REMOVE_QUESTION)).toBeNull();
    expect(screen.queryByText(WORKSPACE_QUESTION)).toBeNull();
    expect(requestsTo(WORKSPACE_URL)).toEqual([
      [WORKSPACE_URL, { data: { user_id: member.id } }],
    ]);
    expect(requestsTo(fixture.memberRemoveUrl)).toHaveLength(0);
    expect(fixture.snackbar).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it.each([
    ["the Close button", "Close"],
    ["the dialog's own close control", "Dismiss dialog"],
  ])(
    "W02: closing with %s ends the flow instead of asking again",
    async (_how, control) => {
      const { onClose } = renderRemoval();
      await click("Remove");
      await screen.findByText(ONLY_WORKSPACE_NOTICE);

      await click(control);

      expect(onClose).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(screen.queryByText(WORKSPACE_QUESTION)).toBeNull();
      expect(screen.queryByText(ONLY_WORKSPACE_NOTICE)).toBeNull();
      expect(fixture.del).toHaveBeenCalledTimes(1);
      expect(requestsTo(fixture.memberRemoveUrl)).toHaveLength(0);

      // Opening the removal again starts from the workspace question, not the old notice.
      await click("Remove from workspace");
      expect(screen.getByText(WORKSPACE_QUESTION)).toBeVisible();
      expect(screen.queryByText(ONLY_WORKSPACE_NOTICE)).toBeNull();
    },
  );

  it.each([
    ["Admin", { orgLevel: LEVELS.ADMIN, organization_role: "Admin" }],
    ["Owner", { orgLevel: LEVELS.OWNER, organization_role: "Owner" }],
    [
      "Admin known only by role name",
      { orgLevel: null, organization_role: "Admin" },
    ],
  ])(
    "W03: an organization %s is still offered removal from the organization",
    async (_role, actor) => {
      fixture.auth = {
        ...fixture.auth,
        user: {
          ...fixture.auth.user,
          organization_role: actor.organization_role,
        },
        orgLevel: actor.orgLevel,
        wsLevel: null,
      };
      serve({ actorIsOrgAdmin: true });
      const { onClose } = renderRemoval();

      await click("Remove");
      expect(await screen.findByText(ORG_REMOVE_QUESTION)).toBeVisible();
      expect(screen.queryByText(ONLY_WORKSPACE_NOTICE)).toBeNull();
      expect(screen.getByRole("button", { name: "Cancel" })).toBeVisible();

      await click("Remove from organization");

      await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
      expect(requestsTo(fixture.memberRemoveUrl)).toEqual([
        [fixture.memberRemoveUrl, { data: { user_id: member.id } }],
      ]);
      expect(fixture.snackbar).toHaveBeenCalledWith(
        "Member removed from organization",
        {
          variant: "success",
        },
      );
    },
  );

  it("W04: a workspace-only admin still removes a member who has other workspaces", async () => {
    serve({ hasOtherWorkspaces: true });
    const { onClose } = renderRemoval();

    await click("Remove");

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(fixture.del).toHaveBeenCalledTimes(1);
    expect(fixture.del).toHaveBeenCalledWith(WORKSPACE_URL, {
      data: { user_id: member.id },
    });
    expect(fixture.snackbar).toHaveBeenCalledWith(
      "Member removed from workspace",
      {
        variant: "success",
      },
    );
    expect(screen.queryByText(ONLY_WORKSPACE_NOTICE)).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
