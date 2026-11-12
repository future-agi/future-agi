/* eslint-disable react/prop-types */
// "Invite team members" is listed for every reader, in its place (last).
// The invite form on the step is for the organization Owner only, as before:
// it sends a request only the Owner may send. A reader who can invite
// anywhere (the workspace switcher offers them "Invite members") is never
// told to ask someone else: they read "Invite teammates from Settings >
// Members." with a link when the organization Members route lets them in,
// else the same line for their workspace's Members page, named as the
// settings menu names it, with a link there, else the switcher's own entry.
// Anyone else reads "Ask an owner or admin ...", with the link only when the
// Members route lets them in. The step counts toward progress for the Owner
// and for a reader who can invite; for anyone else it is listed but not
// counted.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { endpoints } from "src/utils/axios";
import GetStartedView from "../GetStartedView";
import InitialSetup from "../setup-steps/InitialSetup";
import InitialSetupOptions from "../setup-steps/InitialSetupOptions/InitialSetupOptions";
import InviteTeamMembers from "../setup-steps/InitialSetupOptions/InviteTeamMembers";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
const auth = vi.hoisted(() => ({ user: null }));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: http,
}));
// What the auth provider gives: the role it reports is the default
// workspace role, else the organization role; a level it does not know is
// empty.
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: auth.user,
    role: auth.user?.default_workspace_role || auth.user?.organization_role,
    orgLevel: auth.user?.org_level ?? null,
    wsLevel: auth.user?.ws_level ?? null,
    effectiveLevel: auth.user?.effective_level ?? null,
  }),
}));
// What the organization and workspace providers give: the organization role
// from user-info, and the current workspace (its id and role) from the
// reader's default workspace.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    currentOrganizationId: "org-a",
    currentOrganizationRole: auth.user?.organization_role ?? null,
    isReady: true,
  }),
}));
vi.mock("src/contexts/WorkspaceContext", async (importOriginal) => ({
  ...(await importOriginal()),
  useWorkspace: () => ({
    currentWorkspaceId: auth.user?.default_workspace_id ?? null,
    currentWorkspaceRole: auth.user?.default_workspace_role ?? null,
  }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  handleOnDocsClicked: vi.fn(),
  resetUser: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/components/external-content/ExternalContentFrame", () => ({
  default: () => null,
}));
vi.mock("src/components/image", () => ({
  default: ({ alt }) => <img alt={alt} />,
}));
vi.mock("../GetStartedHeaderView", () => ({ default: () => null }));
vi.mock("../try-out-features/TryOutFeatures", () => ({ default: () => null }));
// Step bodies that are not under test. The invite form stays real.
vi.mock("src/sections/project/NewProject/NewObserve", () => ({
  default: () => null,
}));
vi.mock("src/sections/project/NewProject/NewExperiment", () => ({
  default: () => null,
}));

// The readers, as user-info describes them.
const OWNER = { id: "person-o", organization_role: "Owner", org_level: 15 };
// An Owner whose organization level did not come back, in a workspace
// where they are only a viewer.
const OWNER_WITHOUT_LEVEL = {
  id: "person-p",
  organization_role: "Owner",
  org_level: null,
  effective_level: null,
  default_workspace_role: "workspace_viewer",
};
const ADMIN = {
  id: "person-a",
  organization_role: "Admin",
  org_level: 8,
  effective_level: 8,
};
const MEMBER = {
  id: "person-m",
  organization_role: "Member",
  org_level: 3,
  effective_level: 3,
};
const VIEWER = {
  id: "person-v",
  organization_role: "Viewer",
  org_level: 1,
  effective_level: 1,
};
// The reader's current workspace, as the workspace list gives it.
const WORKSPACE_ID = "ws-research";
const WORKSPACE_ROW = {
  id: WORKSPACE_ID,
  name: "research",
  display_name: "Research",
  user_ws_level: 8,
};
// Administers their workspace but is an organization Member: the switcher
// offers them "Invite members", the organization Members route does not let
// them in, and their workspace's Members route does.
const WORKSPACE_ADMIN = {
  id: "person-w",
  organization_role: "Member",
  org_level: 3,
  effective_level: 8,
  default_workspace_role: "workspace_admin",
  default_workspace_id: WORKSPACE_ID,
  ws_enabled: true,
};
// The same reader where workspaces are off: no workspace Members route is
// registered, so neither Members route lets them in.
const WORKSPACE_ADMIN_WITHOUT_WORKSPACES = {
  ...WORKSPACE_ADMIN,
  id: "person-x",
  ws_enabled: false,
};
// An organization Admin whose levels did not come back, in a workspace where
// they are a member: the switcher offers them "Invite members" by their
// organization role, and the Members route lets them in.
const ADMIN_WITHOUT_LEVELS = {
  id: "person-b",
  organization_role: "Admin",
  org_level: null,
  effective_level: null,
  default_workspace_role: "workspace_member",
};

const READERS = {
  Owner: OWNER,
  Admin: ADMIN,
  Member: MEMBER,
  Viewer: VIEWER,
  "workspace admin": WORKSPACE_ADMIN,
};

const INVITE_HERE = "Invite teammates from Settings > Members.";
// The workspace's Members page, named as the settings menu names it: the
// workspace under Settings, then Members.
const INVITE_ON_WORKSPACE =
  "Invite teammates from Settings > Research > Members.";
const WORKSPACE_MEMBERS_PATH =
  "/dashboard/settings/workspace/ws-research/members";
const INVITE_FROM_SWITCHER =
  "Invite teammates from the workspace switcher > Invite members.";
const ASK =
  "Ask an owner or admin of your organization to invite teammates from Settings > Members.";
const FORM_HEADING = "Invite people to collaborate in Future AGI";
const MEMBERS_PATH = "/dashboard/settings/user-management";
const STEP_LINE =
  "Invite teammates to your organization so they can sign in and work with you.";
const STEP_TITLES = [
  "Add provider keys",
  "Create your first dataset",
  "Create your first evaluation",
  "Run your first experiment",
  "Send your first trace",
  "Invite team members",
];

const NOTHING_YET = {
  keys: false,
  dataset: false,
  evaluation: false,
  experiment: false,
  observe: false,
  invite: false,
};
const TWO_DONE = { ...NOTHING_YET, keys: true, dataset: true };
const FIVE_OTHERS_DONE = {
  keys: true,
  dataset: true,
  evaluation: true,
  experiment: true,
  observe: true,
  invite: false,
};

const ok = (result) => Promise.resolve({ data: { status: true, result } });

let checks;
let client;

beforeEach(() => {
  auth.user = OWNER;
  checks = { ...NOTHING_YET };
  window.sessionStorage.clear();
  http.get.mockReset();
  http.post.mockReset();
  http.get.mockImplementation((url) => {
    if (url === endpoints.getStarted.getTabs) return ok({ ...checks });
    // The workspace list answers with its rows at the top level.
    if (url === endpoints.workspaces.list)
      return Promise.resolve({ data: { results: [WORKSPACE_ROW] } });
    if (url === endpoints.settings.v2.deploymentInfo) return ok({ mode: "ee" });
    if (url === endpoints.auth.create_org)
      return ok({ org_name: "Example org", results: [] });
    if (url === endpoints.settings.teams.getMemberList)
      return ok({ results: [] });
    return ok({});
  });
});

afterEach(() => {
  cleanup();
});

const renderUi = (ui, route = "/dashboard/get-started") => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderWithRouter(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
    { route },
  );
};

// Lets the requests a step makes answer and render.
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });

const editionAnswered = () =>
  waitFor(() =>
    expect(
      http.get.mock.calls.some(
        ([url]) => url === endpoints.settings.v2.deploymentInfo,
      ),
    ).toBe(true),
  );

// The step list and the summary next to it, as the page shows them.
const renderListAndSummary = (data) =>
  renderUi(
    <>
      <div data-testid="step-list">
        <InitialSetup
          currentLabel="completed"
          setCurrentLabel={vi.fn()}
          data={data}
        />
      </div>
      <div data-testid="summary">
        <InitialSetupOptions
          currentLabel="completed"
          setCurrentLabel={vi.fn()}
          data={data}
        />
      </div>
    </>,
    "/dashboard/get-started?tab=completed",
  );
const list = () => within(screen.getByTestId("step-list"));
const summary = () => within(screen.getByTestId("summary"));
const listedTitles = () =>
  list()
    .getAllByRole("button")
    .map((button) => button.textContent.replace(/\s*Done$/, "").trim());

const renderInviteStep = async () => {
  renderUi(
    <InviteTeamMembers />,
    "/dashboard/get-started?tab=inviteTeamMembers",
  );
  await editionAnswered();
  await settle();
};

// No part of the invite form is on screen.
const expectNoForm = () => {
  expect(screen.queryByText(FORM_HEADING)).not.toBeInTheDocument();
  expect(screen.queryByText("Add members")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Continue" }),
  ).not.toBeInTheDocument();
};

// The line and link for a reader who can invite and whom the organization
// Members route lets in.
const expectInvitesOnMembers = () => {
  expect(screen.getByText(INVITE_HERE)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to Members" })).toHaveAttribute(
    "href",
    MEMBERS_PATH,
  );
  expect(screen.queryByText(ASK)).not.toBeInTheDocument();
  expectNoForm();
};

// The line and link for a reader who can invite and whom only their
// workspace's Members route lets in.
const expectInvitesOnWorkspaceMembers = async () => {
  expect(await screen.findByText(INVITE_ON_WORKSPACE)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Go to Members" })).toHaveAttribute(
    "href",
    WORKSPACE_MEMBERS_PATH,
  );
  expect(screen.queryByText(/^Ask an owner or admin/)).not.toBeInTheDocument();
  expect(screen.queryByText(INVITE_HERE)).not.toBeInTheDocument();
  expectNoForm();
};

// The line for a reader who cannot invite and cannot open Members.
const expectAsksWithoutLink = () => {
  expect(screen.getByText(ASK)).toBeInTheDocument();
  expect(screen.queryByText(INVITE_HERE)).not.toBeInTheDocument();
  expect(
    screen.queryByRole("link", { name: "Go to Members" }),
  ).not.toBeInTheDocument();
  expectNoForm();
};

describe("Get started: the invite step is listed for every reader", () => {
  it("guard: an Owner's list shows all six steps, the invite step last", () => {
    renderListAndSummary(NOTHING_YET);

    expect(listedTitles()).toEqual(STEP_TITLES);
  });

  it.each([
    ["Admin", ADMIN],
    ["Member", MEMBER],
    ["Viewer", VIEWER],
    ["workspace admin who is an organization Member", WORKSPACE_ADMIN],
  ])(
    "for a %s, the list shows all six steps, the invite step last",
    (_who, user) => {
      auth.user = user;
      renderListAndSummary(NOTHING_YET);

      expect(listedTitles()).toEqual(STEP_TITLES);
    },
  );

  it("guard: for an Owner with the invite check true, the invite step reads 'Done'", () => {
    renderListAndSummary({ ...NOTHING_YET, invite: true });

    expect(
      list().getByRole("button", { name: "Invite team members Done" }),
    ).toBeInTheDocument();
  });

  it.each([["Admin"], ["Member"], ["Viewer"]])(
    "for a %s with the invite check true, the invite step reads 'Done'",
    (role) => {
      auth.user = READERS[role];
      renderListAndSummary({ ...NOTHING_YET, invite: true });

      expect(
        list().getByRole("button", { name: "Invite team members Done" }),
      ).toBeInTheDocument();
    },
  );
});

describe("Get started: what each reader reads on the invite step", () => {
  it("guard: an Owner gets the invite form and no Members line", async () => {
    await renderInviteStep();

    expect(await screen.findByText(FORM_HEADING)).toBeInTheDocument();
    expect(screen.getByText("Add members")).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Continue" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(INVITE_HERE)).not.toBeInTheDocument();
    expect(screen.queryByText(ASK)).not.toBeInTheDocument();
  });

  it("guard: an Owner whose organization level is empty still gets the form and six counted steps", async () => {
    auth.user = OWNER_WITHOUT_LEVEL;
    await renderInviteStep();

    expect(await screen.findByText(FORM_HEADING)).toBeInTheDocument();
    expect(screen.getByText("Add members")).toBeInTheDocument();
    cleanup();

    renderListAndSummary(TWO_DONE);
    expect(list().getByText("2 of 6 done")).toBeInTheDocument();
    expect(summary().getByText("2 of 6 steps done")).toBeInTheDocument();
  });

  it("an Admin reads that teammates are invited from Settings > Members, with the link, and gets no form", async () => {
    auth.user = ADMIN;
    await renderInviteStep();

    expect(screen.getByText(STEP_LINE)).toBeInTheDocument();
    expectInvitesOnMembers();
  });

  it.each([["Member"], ["Viewer"]])(
    "a %s reads 'Ask an owner or admin', with no link and no form",
    async (role) => {
      auth.user = READERS[role];
      await renderInviteStep();

      expect(screen.getByText(STEP_LINE)).toBeInTheDocument();
      expectAsksWithoutLink();
    },
  );

  it("a workspace admin who is an organization Member reads that teammates are invited from their workspace's Members page, with the link there, and gets no form", async () => {
    auth.user = WORKSPACE_ADMIN;
    await renderInviteStep();

    expect(screen.getByText(STEP_LINE)).toBeInTheDocument();
    await expectInvitesOnWorkspaceMembers();
  });

  it("guard: for a workspace admin who is an organization Member, the step sends no request of the invite form", async () => {
    auth.user = WORKSPACE_ADMIN;
    await renderInviteStep();
    await settle();

    const asked = http.get.mock.calls.map(([url]) => url);
    expect(asked).not.toContain(endpoints.auth.user_onboarding_info);
    expect(asked).not.toContain(endpoints.auth.create_org);
    expect(asked).not.toContain(endpoints.settings.teams.getMemberList);
    expect(http.post).not.toHaveBeenCalled();
  });

  it("an organization Admin whose levels come back empty reads that teammates are invited from Settings > Members, with the link, and gets no form", async () => {
    auth.user = ADMIN_WITHOUT_LEVELS;
    await renderInviteStep();

    expectInvitesOnMembers();
  });

  it("a workspace admin where workspaces are off, whom neither Members route lets in, reads the switcher's Invite members, with no link, and gets no form", async () => {
    auth.user = WORKSPACE_ADMIN_WITHOUT_WORKSPACES;
    await renderInviteStep();

    expect(await screen.findByText(INVITE_FROM_SWITCHER)).toBeInTheDocument();
    expect(
      screen.queryByText(/^Ask an owner or admin/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Go to Members" }),
    ).not.toBeInTheDocument();
    expectNoForm();
  });

  it.each([["Admin"], ["Member"]])(
    "for a %s, the step sends no request of the invite form",
    async (role) => {
      auth.user = READERS[role];
      await renderInviteStep();
      await settle();

      const asked = http.get.mock.calls.map(([url]) => url);
      expect(asked).not.toContain(endpoints.auth.user_onboarding_info);
      expect(asked).not.toContain(endpoints.auth.create_org);
      expect(asked).not.toContain(endpoints.settings.teams.getMemberList);
      expect(http.post).not.toHaveBeenCalled();
    },
  );
});

describe("Get started: progress counts the invite step for whoever can invite", () => {
  it("guard: an Owner with two steps done reads '2 of 6' in the list and the summary", () => {
    renderListAndSummary(TWO_DONE);

    expect(list().getByText("2 of 6 done")).toBeInTheDocument();
    expect(summary().getByText("2 of 6 steps done")).toBeInTheDocument();
  });

  it("an Admin with two steps done reads '2 of 6' in the list and the summary", () => {
    auth.user = ADMIN;
    renderListAndSummary(TWO_DONE);

    expect(list().getByText("2 of 6 done")).toBeInTheDocument();
    expect(summary().getByText("2 of 6 steps done")).toBeInTheDocument();
  });

  it("an Admin with the five other steps done reads '5 of 6', not complete", () => {
    auth.user = ADMIN;
    renderListAndSummary(FIVE_OTHERS_DONE);

    expect(list().getByText("5 of 6 done")).toBeInTheDocument();
    expect(summary().getByText("5 of 6 steps done")).toBeInTheDocument();
    expect(summary().queryByText("Setup is complete")).not.toBeInTheDocument();
  });

  it("an Admin with no step done reads '0 of 6 steps done' in the summary", () => {
    auth.user = ADMIN;
    renderListAndSummary(NOTHING_YET);

    expect(list().getByText("0 of 6 done")).toBeInTheDocument();
    expect(summary().getByText("0 of 6 steps done")).toBeInTheDocument();
  });

  it.each([
    ["workspace admin who is an organization Member", WORKSPACE_ADMIN],
    ["organization Admin whose levels come back empty", ADMIN_WITHOUT_LEVELS],
    [
      "workspace admin where workspaces are off",
      WORKSPACE_ADMIN_WITHOUT_WORKSPACES,
    ],
  ])(
    "for a %s, with two steps done, the list and the summary both read '2 of 6'",
    (_who, user) => {
      auth.user = user;
      renderListAndSummary(TWO_DONE);

      expect(list().getByText("2 of 6 done")).toBeInTheDocument();
      expect(summary().getByText("2 of 6 steps done")).toBeInTheDocument();
    },
  );

  it.each([
    ["Member", MEMBER],
    ["Viewer", VIEWER],
  ])(
    "guard: for a %s, with two steps done, the list and the summary both read '2 of 5'",
    (_who, user) => {
      auth.user = user;
      renderListAndSummary(TWO_DONE);

      const listed = list().getByText(/^\d+ of \d+ done$/).textContent;
      const counted = summary().getByText(/steps done$/).textContent;
      expect(listed).toBe("2 of 5 done");
      expect(counted).toBe("2 of 5 steps done");
    },
  );

  it.each([["Member"], ["Viewer"]])(
    "guard: for a %s, with their five steps done, the summary reads 'Setup is complete'",
    (role) => {
      auth.user = READERS[role];
      renderListAndSummary(FIVE_OTHERS_DONE);

      expect(list().getByText("5 of 5 done")).toBeInTheDocument();
      expect(summary().getByText("Setup is complete")).toBeInTheDocument();
      expect(summary().queryByText(/steps done$/)).not.toBeInTheDocument();
    },
  );
});

describe("Get started: every way onto the invite step opens a listed step", () => {
  const checksLoaded = async () => {
    await waitFor(() =>
      expect(client.getQueryState(["currentLabel"])?.dataUpdateCount ?? 0).toBe(
        1,
      ),
    );
    await act(async () => {});
    await act(async () => {});
  };
  const tabInUrl = () => new URLSearchParams(window.location.search).get("tab");

  // The invite step is open, it is in the step list, and it reads the
  // reader's line, with no form.
  const expectListedInviteStep = async (role) => {
    expect(tabInUrl()).toBe("inviteTeamMembers");
    await editionAnswered();
    await settle();
    expect(screen.getByText(STEP_LINE)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^Invite team members( Done)?$/ }),
    ).toBeInTheDocument();
    if (role === "Admin") expectInvitesOnMembers();
    else if (role === "workspace admin")
      await expectInvitesOnWorkspaceMembers();
    else expectAsksWithoutLink();
  };

  it.each([["Member"], ["Admin"], ["workspace admin"]])(
    "for a %s, Skip on 'Send your first trace'",
    async (role) => {
      auth.user = READERS[role];
      checks = { ...TWO_DONE };
      renderUi(
        <GetStartedView />,
        "/dashboard/get-started?tab=SetupObsabilityInApplication",
      );
      await checksLoaded();

      fireEvent.click(screen.getByRole("button", { name: "Skip" }));
      await waitFor(() => expect(tabInUrl()).toBe("inviteTeamMembers"));

      await expectListedInviteStep(role);
    },
  );

  it.each([["Member"], ["Admin"], ["workspace admin"]])(
    "for a %s, the address ?tab=inviteTeamMembers",
    async (role) => {
      auth.user = READERS[role];
      checks = { ...TWO_DONE };
      renderUi(
        <GetStartedView />,
        "/dashboard/get-started?tab=inviteTeamMembers",
      );
      await checksLoaded();

      await expectListedInviteStep(role);
    },
  );

  it.each([["Member"], ["Admin"], ["workspace admin"]])(
    "for a %s, the step remembered in this tab",
    async (role) => {
      auth.user = READERS[role];
      checks = { ...TWO_DONE };
      window.sessionStorage.setItem(
        `getStarted.lastStep:${auth.user.id}:org-a`,
        "inviteTeamMembers",
      );
      renderUi(<GetStartedView />, "/dashboard/get-started");
      await checksLoaded();

      await expectListedInviteStep(role);
    },
  );

  it("an Admin with the five other steps done opens the invite step on arrival", async () => {
    auth.user = ADMIN;
    checks = { ...FIVE_OTHERS_DONE };
    renderUi(<GetStartedView />, "/dashboard/get-started");
    await checksLoaded();

    await expectListedInviteStep("Admin");
  });
});
