import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import WorkspaceGeneral from "../WorkspaceGeneral";

// Settings > Workspace > General states the workspace's name, its ID and the
// reader's own access, and always shows Sharing, held for this release with
// its controls on screen and disabled, without asking the server for it.

const WS_ID = "6f1c2a7e-3b4d-4e5f-8a9b-0c1d2e3f4a5b";
const SHARING_POLICY_URL = `/model-hub/workspaces/${WS_ID}/sharing-policy/`;
const RENAME_REASON = "Only workspace admins can rename this workspace.";
const ORG_ROLE_APPLIES = "Your organization role applies in this workspace.";
const LOAD_FAILED =
  "This workspace could not be loaded. Reload the page, or pick another workspace from the switcher.";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  enqueueSnackbar: vi.fn(),
  updateWorkspaceName: vi.fn(),
  role: "Owner",
  user: { organization_role: "Owner" },
  list: { workspace: null, isLoading: false, isError: false },
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => h.get(...args),
    put: (...args) => h.put(...args),
  },
  endpoints: {
    workspace: { workspaceUpdate: (id) => `/workspaces/${id}/` },
    develop: {
      w4: {
        sharingPolicy: (id) => `/model-hub/workspaces/${id}/sharing-policy/`,
      },
    },
  },
}));
vi.mock("src/api/workspaces/list", () => ({
  workspacesListKey: ["workspaces-list"],
  useWorkspaceFromList: () => h.list,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: h.role, user: h.user }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: WS_ID,
    updateWorkspaceName: h.updateWorkspaceName,
  }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: h.enqueueSnackbar }),
}));
vi.mock("src/components/loading-screen", () => ({
  LoadingScreen: () => <p>Loading workspace</p>,
}));

const theme = createTheme({ palette: palette("light"), typography });

const workspace = (fields = {}) => ({
  id: WS_ID,
  name: "design",
  display_name: "Design",
  user_ws_level: 8,
  ...fields,
});

function signIn({ role, orgRole = role, level }) {
  h.role = role;
  h.user = { organization_role: orgRole };
  h.list = {
    workspace: workspace(level === undefined ? {} : { user_ws_level: level }),
    isLoading: false,
    isError: false,
  };
}

function showPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <ThemeProvider theme={theme}>
          <MemoryRouter
            initialEntries={[`/dashboard/settings/workspace/${WS_ID}/general`]}
          >
            <Routes>
              <Route
                path="/dashboard/settings/workspace/:workspaceId/general"
                element={<WorkspaceGeneral />}
              />
              <Route
                path="/dashboard/settings/workspace/:workspaceId/members"
                element={<p>Members page</p>}
              />
            </Routes>
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

const section = (testId) => within(screen.getByTestId(testId));

// The workspace as the list sends it when the reader has no level recorded
// in it: the level is null, or (from an older list) not there at all.
const NO_LEVEL = {
  null: { user_ws_level: null },
  absent: {},
};

// Every line Your access renders carries words: none is empty, and none
// prints a missing value.
function expectEveryAccessLineFilled() {
  const node = screen.getByTestId("workspace-access-section");
  expect(node.textContent).not.toMatch(/undefined|null|NaN/);
  const lines = Array.from(node.querySelectorAll("*")).filter(
    (el) => el.children.length === 0,
  );
  expect(lines.length).toBeGreaterThan(0);
  expect(
    lines.filter((el) => !el.textContent.trim()).map((el) => el.outerHTML),
  ).toEqual([]);
}

function setClipboard(value) {
  Object.defineProperty(window.navigator, "clipboard", {
    configurable: true,
    value,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  h.get.mockReset().mockRejectedValue({ statusCode: 404 });
  h.put.mockReset().mockImplementation(async (_url, payload) => ({
    data: { workspace: { id: WS_ID, ...payload } },
  }));
  signIn({ role: "Owner", level: 8 });
});

afterEach(() => {
  cleanup();
  delete window.navigator.clipboard;
  window.getSelection()?.removeAllRanges();
});

describe("Workspace General page heading", () => {
  it("is headed General, with one line under it, and titles the tab in sentence case", async () => {
    showPage();
    expect(
      screen.getByRole("heading", { level: 1, name: "General" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Name, ID and access for this workspace."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Manage workspace settings")).toBeNull();
    await waitFor(() => expect(document.title).toBe("Workspace settings"));
  });
});

describe("Workspace name", () => {
  it("keeps the name field, labelled Workspace name, and enables Save only once the name changes", () => {
    showPage();
    const name = section("workspace-name-section");
    const field = name.getByRole("textbox", { name: "Workspace name" });
    expect(field).toHaveValue("Design");
    expect(field).toBeEnabled();
    const save = name.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    fireEvent.change(field, { target: { value: "Design team" } });
    expect(save).toBeEnabled();
    fireEvent.change(field, { target: { value: "Design" } });
    expect(save).toBeDisabled();
    expect(screen.queryByText(RENAME_REASON)).toBeNull();
  });

  it("saves a new name with the same request and the same confirmation as before", async () => {
    showPage();
    const field = screen.getByRole("textbox", { name: "Workspace name" });
    fireEvent.change(field, { target: { value: "Design team" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(h.enqueueSnackbar).toHaveBeenCalledWith("Workspace updated", {
        variant: "success",
      }),
    );
    expect(h.put).toHaveBeenCalledTimes(1);
    expect(h.put).toHaveBeenCalledWith(`/workspaces/${WS_ID}/`, {
      display_name: "Design team",
    });
    expect(h.updateWorkspaceName).toHaveBeenCalledWith("Design team");
  });

  it("tells a reader who may not rename the workspace why the field is disabled", () => {
    signIn({ role: "workspace_member", orgRole: "Member", level: 3 });
    showPage();
    const name = section("workspace-name-section");
    const field = name.getByRole("textbox", { name: "Workspace name" });
    expect(field).toBeDisabled();
    expect(field).toHaveValue("Design");
    expect(name.getByText(RENAME_REASON)).toBeVisible();
    expect(field).toHaveAccessibleDescription(RENAME_REASON);
    // As before, a reader who cannot rename is offered no Save.
    expect(name.queryByRole("button", { name: "Save" })).toBeNull();
  });
});

describe("Workspace ID", () => {
  it("shows the ID read-only in code style, with Copy and one helper line", () => {
    showPage();
    const id = section("workspace-id-section");
    expect(id.getByText("Workspace ID")).toBeInTheDocument();
    const value = id.getByText(WS_ID);
    expect(value.tagName).toBe("CODE");
    expect(id.queryByRole("textbox")).toBeNull();
    expect(
      id.getByText("Use this ID with the API and the SDK."),
    ).toBeInTheDocument();
    const copy = id.getByRole("button", { name: "Copy workspace ID" });
    expect(copy).toHaveTextContent("Copy");
    expect(copy).toBeEnabled();
  });

  it("copies the ID from the click itself and confirms with a toast", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard({ writeText });
    showPage();
    fireEvent.click(screen.getByRole("button", { name: "Copy workspace ID" }));
    // Called inside the click handler, before any await.
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith(WS_ID);
    await waitFor(() =>
      expect(h.enqueueSnackbar).toHaveBeenCalledWith("Workspace ID copied", {
        variant: "success",
      }),
    );
  });

  it("selects the ID when the clipboard refuses, without throwing", async () => {
    const errors = vi.fn();
    window.addEventListener("error", errors);
    const writeText = vi.fn().mockRejectedValue(new Error("Not allowed"));
    setClipboard({ writeText });
    showPage();
    fireEvent.click(screen.getByRole("button", { name: "Copy workspace ID" }));
    expect(writeText).toHaveBeenCalledWith(WS_ID);
    await waitFor(() => expect(window.getSelection().toString()).toBe(WS_ID));
    expect(h.enqueueSnackbar).not.toHaveBeenCalledWith(
      "Workspace ID copied",
      expect.anything(),
    );
    window.removeEventListener("error", errors);
    expect(errors).not.toHaveBeenCalled();
  });

  it("selects the ID when the page has no clipboard at all, without throwing", async () => {
    const errors = vi.fn();
    window.addEventListener("error", errors);
    setClipboard(undefined);
    showPage();
    fireEvent.click(screen.getByRole("button", { name: "Copy workspace ID" }));
    await waitFor(() => expect(window.getSelection().toString()).toBe(WS_ID));
    window.removeEventListener("error", errors);
    expect(errors).not.toHaveBeenCalled();
    expect(h.enqueueSnackbar).not.toHaveBeenCalled();
  });
});

describe("Your access", () => {
  it.each([
    ["Owner", "Owner", 8, "Workspace Admin", "Organization role: Owner"],
    ["Admin", "Admin", 1, "Workspace Admin", "Organization role: Admin"],
    [
      "workspace_admin",
      "Member",
      8,
      "Workspace Admin",
      "Organization role: Member",
    ],
    [
      "workspace_member",
      "Member",
      3,
      "Workspace Member",
      "Organization role: Member",
    ],
    [
      "workspace_viewer",
      "Viewer",
      1,
      "Workspace Viewer",
      "Organization role: Viewer",
    ],
  ])(
    "states the access of a reader with role %s (organization %s, level %i) in words",
    (role, orgRole, level, words, detail) => {
      signIn({ role, orgRole, level });
      showPage();
      const access = section("workspace-access-section");
      expect(access.getByText("Your access")).toBeInTheDocument();
      expect(access.getByText(words)).toBeVisible();
      expect(access.getByText(detail)).toBeVisible();
    },
  );

  it("shows the organization role when the reader has no workspace level", () => {
    signIn({ role: "Member", level: undefined });
    h.list.workspace = { id: WS_ID, name: "design", display_name: "Design" };
    showPage();
    const access = section("workspace-access-section");
    expect(access.getByText("Organization Member")).toBeVisible();
    expect(
      access.queryByText("Your organization role applies in this workspace."),
    ).toBeNull();
    const text = screen.getByTestId("workspace-access-section").textContent;
    expect(text).not.toMatch(/undefined|null|NaN|Workspace Viewer/);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("names an organization owner with no workspace level a workspace admin, from the organization role", () => {
    signIn({ role: "Owner", level: undefined });
    h.list.workspace = { id: WS_ID, name: "design", display_name: "Design" };
    showPage();
    const access = section("workspace-access-section");
    expect(access.getByText("Workspace Admin")).toBeVisible();
    expect(access.getByText("Organization role: Owner")).toBeVisible();
  });

  it.each([
    ["Owner", "null"],
    ["Owner", "absent"],
    ["Admin", "null"],
    ["Admin", "absent"],
  ])(
    "tells an organization %s with no workspace level (level %s) that their organization role applies in this workspace",
    (orgRole, shape) => {
      signIn({ role: orgRole, level: undefined });
      h.list.workspace = {
        id: WS_ID,
        name: "design",
        display_name: "Design",
        ...NO_LEVEL[shape],
      };
      showPage();
      const access = section("workspace-access-section");
      expect(access.getByText("Workspace Admin")).toBeVisible();
      expect(access.getByText(`Organization role: ${orgRole}`)).toBeVisible();
      expect(access.getByText(ORG_ROLE_APPLIES)).toBeVisible();
      expectEveryAccessLineFilled();
    },
  );

  it.each([
    ["Member", "null"],
    ["Member", "absent"],
    ["Viewer", "null"],
    ["Viewer", "absent"],
  ])(
    "does not tell an organization %s with no workspace level (level %s) that their organization role applies, since the server gives them no access to the workspace",
    (orgRole, shape) => {
      signIn({ role: orgRole, level: undefined });
      h.list.workspace = {
        id: WS_ID,
        name: "design",
        display_name: "Design",
        ...NO_LEVEL[shape],
      };
      showPage();
      const access = section("workspace-access-section");
      expect(access.getByText(`Organization ${orgRole}`)).toBeVisible();
      expect(access.queryByText(ORG_ROLE_APPLIES)).toBeNull();
      expect(
        access.queryByText(/^Workspace (Admin|Member|Viewer)$/),
      ).toBeNull();
      expectEveryAccessLineFilled();
    },
  );

  it("links Manage members to this workspace's Members page for a workspace admin", async () => {
    showPage();
    const link = section("workspace-access-section").getByRole("link", {
      name: "Manage members",
    });
    expect(link).toHaveAttribute(
      "href",
      `/dashboard/settings/workspace/${WS_ID}/members`,
    );
    fireEvent.click(link);
    expect(await screen.findByText("Members page")).toBeInTheDocument();
  });

  it("offers no Manage members link to a reader the Members page does not admit", () => {
    signIn({ role: "workspace_member", orgRole: "Member", level: 3 });
    showPage();
    expect(screen.queryByRole("link", { name: "Manage members" })).toBeNull();
  });
});

describe("Sharing", () => {
  it.each([
    ["a workspace admin", { role: "Owner", level: 8 }],
    [
      "a reader who is not a workspace admin",
      { role: "workspace_member", orgRole: "Member", level: 3 },
    ],
  ])(
    "is always shown to %s, with both controls disabled and the shared release line, and asks the server nothing",
    async (_who, reader) => {
      signIn(reader);
      showPage();
      const card = screen.getByTestId("workspace-sharing-card");
      const sharing = within(card);
      expect(sharing.getByText("Sharing")).toBeVisible();
      expect(sharing.getByText(NOT_IN_RELEASE)).toBeVisible();

      const publicLinks = sharing.getByRole("checkbox", {
        name: "Public links",
      });
      expect(publicLinks).toBeDisabled();
      expect(publicLinks).not.toBeChecked();
      expect(sharing.getByText("Links expire after at most")).toBeVisible();
      const maxDays = sharing.getByLabelText(
        "Longest a public link lasts, in days",
      );
      expect(maxDays).toHaveAttribute("aria-disabled", "true");
      expect(maxDays.textContent.replace(/\u200b/g, "").trim()).toBe("");

      // Give any query a turn to start: none may go out.
      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(h.get.mock.calls.map(([url]) => url)).not.toContain(
        SHARING_POLICY_URL,
      );
      expect(h.get).not.toHaveBeenCalled();
      expect(h.put).not.toHaveBeenCalled();
    },
  );

  it("leaves the ID and Copy working for a reader who is not a workspace admin", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard({ writeText });
    signIn({ role: "workspace_viewer", orgRole: "Viewer", level: 1 });
    showPage();
    expect(screen.getByText(WS_ID)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Copy workspace ID" }));
    expect(writeText).toHaveBeenCalledWith(WS_ID);
    await waitFor(() =>
      expect(h.enqueueSnackbar).toHaveBeenCalledWith("Workspace ID copied", {
        variant: "success",
      }),
    );
  });
});

describe("Workspace General with existing data", () => {
  it("keeps today's message for a workspace that cannot be loaded, and renders none of the new sections", async () => {
    h.list = { workspace: null, isLoading: false, isError: true };
    showPage();
    expect(screen.getByText(LOAD_FAILED)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByText("Workspace ID")).toBeNull();
    expect(screen.queryByText("Your access")).toBeNull();
    expect(screen.queryByTestId("workspace-sharing-card")).toBeNull();
    expect(screen.queryByText(NOT_IN_RELEASE)).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(h.get).not.toHaveBeenCalled();
  });

  it("offers no delete, archive or transfer, and promises nothing for later", () => {
    showPage();
    const names = [
      ...screen.queryAllByRole("button"),
      ...screen.queryAllByRole("link"),
    ].map((el) => el.textContent);
    names.forEach((name) =>
      expect(name).not.toMatch(/delete|archive|transfer|remove/i),
    );
    expect(document.body.textContent).not.toMatch(
      /coming soon|soon|later this|next release/i,
    );
  });
});
