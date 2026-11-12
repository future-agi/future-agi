/* eslint-disable react/prop-types */
// Coming back to Get started, with the browser's Back button (the address
// still names the step) or with the left-menu entry (it does not), reopens
// the step the user was on. Only a first visit opens the first unfinished
// step. What is remembered belongs to one person in one organization.
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
import { RouterLink } from "src/routes/components";
import GetStartedView from "../GetStartedView";

const http = vi.hoisted(() => ({ get: vi.fn() }));
const auth = vi.hoisted(() => ({
  user: { id: "person-a", organization_role: "Owner" },
}));
// The active organization, as the organization switch leaves it in this tab.
const org = vi.hoisted(() => ({ id: "org-a" }));

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
// The organization provider also gives the organization role from user-info.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    currentOrganizationId: org.id,
    currentOrganizationRole: auth.user?.organization_role ?? null,
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
// The video card's thumbnail (a lazy image) is not under test.
vi.mock("src/components/image", () => ({
  default: ({ alt }) => <img alt={alt} />,
}));
vi.mock("../GetStartedHeaderView", () => ({ default: () => null }));
vi.mock("../try-out-features/TryOutFeatures", () => ({ default: () => null }));
// Heavy step bodies that are not under test; each step's own title, line and
// buttons stay real.
vi.mock("src/sections/project/NewProject/NewObserve", () => ({
  default: () => null,
}));
vi.mock("src/sections/auth/jwt/setup-org", () => ({
  default: () => null,
}));

class FakeRequest {}

// A new account that has added provider keys and nothing else: the first
// unfinished step is "Create your first dataset".
const KEYS_ONLY = {
  keys: true,
  dataset: false,
  evaluation: false,
  experiment: false,
  observe: false,
  invite: false,
};
// A brand-new account: the first unfinished step is "Add keys".
const NOTHING_YET = { ...KEYS_ONLY, keys: false };
const ALL_DONE = {
  keys: true,
  dataset: true,
  evaluation: true,
  experiment: true,
  observe: true,
  invite: true,
};

// What a person reads at the top of the open step: its title and its line.
const STEP_HEADER = {
  addKeys: [
    "Add provider keys",
    "Add a key for each model provider you use so your prompts and experiments can call its models.",
  ],
  createFirstDataset: [
    "Create your first dataset",
    "Create a dataset to hold the examples you test your prompts and models against.",
  ],
  createFirstEvaluation: [
    "Create your first evaluation",
    "Create an evaluation to score your model outputs automatically instead of checking each one by hand.",
  ],
  RunFirstExperiment: [
    "Run your first experiment",
    "Run an experiment on a dataset to compare prompts and models side by side and keep the one that works best.",
  ],
  SetupObsabilityInApplication: [
    "Send your first trace",
    "Send traces from your application so you can follow each request and spot problems in production.",
  ],
  inviteTeamMembers: [
    "Invite team members",
    "Invite teammates to your organization so they can sign in and work with you.",
  ],
};
const ALL_SET_UP = "Setup is complete";
// The summary while steps are left reads "{done} of {shown} steps done".
const SUMMARY_COUNT = /steps done$/;
// The steps that count for a reader who cannot invite: all but the invite
// step, which is listed for them but not counted.
const LISTED_FOR_OTHER_ROLES = [
  "addKeys",
  "createFirstDataset",
  "createFirstEvaluation",
  "RunFirstExperiment",
  "SetupObsabilityInApplication",
];

let checks;
let client;
let view;

const response = (result) => ({
  data: { status: true, result: { ...result } },
  status: 200,
  request: new FakeRequest(),
});
const EMPTY = { data: { status: true, result: {} } };

// The left menu's "Get started" entry: a router link to the bare path. The
// page does not remount when it is clicked.
const page = () => (
  <QueryClientProvider client={client}>
    <RouterLink href="/dashboard/get-started">Get started</RouterLink>
    <GetStartedView />
  </QueryClientProvider>
);

const renderView = (route, seed) => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  seed?.(client);
  view = renderWithRouter(page(), { route });
  return view;
};
const rerenderView = () => view.rerender(page());

const expectOpenStep = (label) => {
  const [title, line] = STEP_HEADER[label];
  const header = screen.getByText(line).parentElement;
  expect(within(header).getByText(title)).toBeVisible();
};
const tabInUrl = () => new URLSearchParams(window.location.search).get("tab");
const checkUpdates = () =>
  client.getQueryState(["currentLabel"])?.dataUpdateCount ?? 0;

// This visit's checks have answered and every effect has run.
const checksLoaded = async () => {
  await waitFor(() => expect(checkUpdates()).toBe(1));
  await act(async () => {});
  await act(async () => {});
};

// The user picks a step from the list on the left.
const pickStep = (label) =>
  fireEvent.click(screen.getByRole("button", { name: STEP_HEADER[label][0] }));

// The user leaves Get started (a step's button, another menu entry, or the
// reload after an organization switch).
const leave = () => {
  cleanup();
  window.history.pushState({}, "", "/dashboard/develop");
};

// In this organization, the user finishes the last step with "Complete the
// setup", so the page remembers the "You are all set up" view.
const completeSetup = async () => {
  checks = { ...ALL_DONE };
  renderView("/dashboard/get-started?tab=inviteTeamMembers");
  await checksLoaded();
  expectOpenStep("inviteTeamMembers");
  fireEvent.click(screen.getByRole("button", { name: "See summary" }));
  expect(await screen.findByText(ALL_SET_UP)).toBeVisible();
};

beforeEach(() => {
  checks = { ...KEYS_ONLY };
  auth.user = { id: "person-a", organization_role: "Owner" };
  org.id = "org-a";
  http.get.mockReset();
  http.get.mockImplementation((url) =>
    Promise.resolve(
      url === endpoints.getStarted.getTabs ? response(checks) : EMPTY,
    ),
  );
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe("Get started: coming back reopens the step the user left", () => {
  it("browser Back to a step's address reopens that step, not 'Add keys'", async () => {
    // WALK-A: from 'Run your first experiment', 'Go to datasets', then Back.
    checks = { ...NOTHING_YET };
    renderView("/dashboard/get-started?tab=RunFirstExperiment");
    await checksLoaded();

    expectOpenStep("RunFirstExperiment");
    expect(tabInUrl()).toBe("RunFirstExperiment");
  });

  it("the left-menu entry (no step in the address) reopens the step the user chose", async () => {
    renderView("/dashboard/get-started");
    await checksLoaded();
    expectOpenStep("createFirstDataset");

    pickStep("SetupObsabilityInApplication");
    await waitFor(() => expectOpenStep("SetupObsabilityInApplication"));
    leave();

    renderView("/dashboard/get-started");
    await checksLoaded();

    expectOpenStep("SetupObsabilityInApplication");
    expect(tabInUrl()).toBe("SetupObsabilityInApplication");
  });

  it("reopens the step the user was on even after finishing it elsewhere", async () => {
    // The first visit opened 'Create your first dataset'; the user clicked
    // 'Create Dataset', made one, and came back with the left menu.
    renderView("/dashboard/get-started");
    await checksLoaded();
    expectOpenStep("createFirstDataset");
    leave();

    checks = { ...KEYS_ONLY, dataset: true };
    renderView("/dashboard/get-started");
    await checksLoaded();

    expectOpenStep("createFirstDataset");
    expect(tabInUrl()).toBe("createFirstDataset");
  });

  it("a step finished elsewhere says it is done when the user comes back to it", async () => {
    renderView("/dashboard/get-started");
    await checksLoaded();
    expectOpenStep("createFirstDataset");
    expect(screen.queryByText("This step is done.")).not.toBeInTheDocument();
    leave();

    checks = { ...KEYS_ONLY, dataset: true };
    renderView("/dashboard/get-started");
    await checksLoaded();

    expectOpenStep("createFirstDataset");
    expect(screen.getByText("This step is done.")).toBeVisible();
  });

  it("clicking 'Get started' in the left menu while on a step keeps that step open", async () => {
    renderView("/dashboard/get-started?tab=createFirstEvaluation");
    await checksLoaded();
    expectOpenStep("createFirstEvaluation");

    fireEvent.click(screen.getByRole("link", { name: "Get started" }));
    await act(async () => {});

    expect(screen.queryByText(ALL_SET_UP)).not.toBeInTheDocument();
    expect(screen.queryByText(SUMMARY_COUNT)).not.toBeInTheDocument();
    expectOpenStep("createFirstEvaluation");
    await waitFor(() => expect(tabInUrl()).toBe("createFirstEvaluation"));
  });

  it("clicking 'Get started' in the left menu keeps the step the user picked on this visit", async () => {
    renderView("/dashboard/get-started");
    await checksLoaded();
    expectOpenStep("createFirstDataset");
    pickStep("RunFirstExperiment");
    await waitFor(() => expectOpenStep("RunFirstExperiment"));

    fireEvent.click(screen.getByRole("link", { name: "Get started" }));
    await act(async () => {});

    expect(screen.queryByText(ALL_SET_UP)).not.toBeInTheDocument();
    expect(screen.queryByText(SUMMARY_COUNT)).not.toBeInTheDocument();
    expectOpenStep("RunFirstExperiment");
    await waitFor(() => expect(tabInUrl()).toBe("RunFirstExperiment"));
  });

  it.each([
    ["another query in the address", "/dashboard/get-started?ref=x"],
    ["an unknown step in the address", "/dashboard/get-started?tab=noSuchStep"],
  ])(
    "with a step to return to and %s, reopens that step",
    async (_case, route) => {
      renderView("/dashboard/get-started");
      await checksLoaded();
      pickStep("SetupObsabilityInApplication");
      await waitFor(() => expectOpenStep("SetupObsabilityInApplication"));
      leave();

      renderView(route);
      await checksLoaded();

      expect(screen.queryByText(ALL_SET_UP)).not.toBeInTheDocument();
      expect(screen.queryByText(SUMMARY_COUNT)).not.toBeInTheDocument();
      expectOpenStep("SetupObsabilityInApplication");
      expect(tabInUrl()).toBe("SetupObsabilityInApplication");
    },
  );

  it("guard: keeps another query in the address when it writes the step", async () => {
    renderView("/dashboard/get-started?ref=x");
    await checksLoaded();

    expectOpenStep("createFirstDataset");
    expect(new URLSearchParams(window.location.search).get("ref")).toBe("x");
  });

  // Guards (pass at the base too): the cases that must keep opening the
  // first unfinished step.
  it("guard: leaving before the checks answer does not pin 'Add keys'", async () => {
    http.get.mockImplementation((url) =>
      url === endpoints.getStarted.getTabs
        ? new Promise(() => {})
        : Promise.resolve(EMPTY),
    );
    renderView("/dashboard/get-started");
    await act(async () => {});
    expect(tabInUrl()).toBe("addKeys");
    leave();

    http.get.mockImplementation((url) =>
      Promise.resolve(
        url === endpoints.getStarted.getTabs ? response(checks) : EMPTY,
      ),
    );
    renderView("/dashboard/get-started");
    await checksLoaded();

    expectOpenStep("createFirstDataset");
  });

  it("guard: an unknown step in the address opens the first unfinished step", async () => {
    renderView("/dashboard/get-started?tab=noSuchStep");
    await checksLoaded();

    expectOpenStep("createFirstDataset");
    expect(tabInUrl()).toBe("createFirstDataset");
  });

  it("guard: with checks loaded moments ago, the bare address opens the first unfinished step", async () => {
    renderView("/dashboard/get-started", (queryClient) =>
      queryClient.setQueryData(["currentLabel"], response(KEYS_ONLY)),
    );
    await act(async () => {});
    await act(async () => {});

    expectOpenStep("createFirstDataset");
    expect(tabInUrl()).toBe("createFirstDataset");
  });

  it("guard: another person signed in on the same tab starts at their own first unfinished step", async () => {
    renderView("/dashboard/get-started");
    await checksLoaded();
    pickStep("SetupObsabilityInApplication");
    await waitFor(() => expectOpenStep("SetupObsabilityInApplication"));
    leave();

    auth.user = { id: "person-b", organization_role: "Owner" };
    renderView("/dashboard/get-started");
    await checksLoaded();

    expectOpenStep("createFirstDataset");
  });
});

describe("Get started: what is remembered stays in its organization", () => {
  it("after finishing setup in one organization, a new organization opens at its first unfinished step", async () => {
    await completeSetup();
    // The organization switch keeps this tab's storage and reloads the app.
    leave();
    org.id = "org-b";
    checks = { ...NOTHING_YET };

    renderView("/dashboard/get-started");
    await checksLoaded();

    expect(screen.queryByText(ALL_SET_UP)).not.toBeInTheDocument();
    expect(screen.queryByText(SUMMARY_COUNT)).not.toBeInTheDocument();
    expectOpenStep("addKeys");
    expect(tabInUrl()).toBe("addKeys");

    // Back in the first organization, its own step comes back.
    leave();
    org.id = "org-a";
    checks = { ...ALL_DONE };
    renderView("/dashboard/get-started");
    await checksLoaded();

    expect(screen.getByText(ALL_SET_UP)).toBeVisible();
  });

  it("after a switch, browser Back to the other organization's 'all set up' address opens this organization's first unfinished step", async () => {
    // completeSetup leaves the address on the "all set up" view; the switch
    // reloads the app elsewhere, and Back returns to that address.
    await completeSetup();
    expect(tabInUrl()).toBe("completed");
    leave();
    org.id = "org-b";
    checks = { ...NOTHING_YET };

    renderView("/dashboard/get-started?tab=completed");
    await checksLoaded();

    expect(screen.queryByText(ALL_SET_UP)).not.toBeInTheDocument();
    expect(screen.queryByText(SUMMARY_COUNT)).not.toBeInTheDocument();
    expectOpenStep("addKeys");
    expect(tabInUrl()).toBe("addKeys");
  });

  it("guard: browser Back to this organization's 'all set up' address reopens it", async () => {
    await completeSetup();
    leave();

    renderView("/dashboard/get-started?tab=completed");
    await checksLoaded();

    expect(screen.getByText(ALL_SET_UP)).toBeVisible();
    expect(tabInUrl()).toBe("completed");
  });

  it("when the organization changes while the page is open, the page starts over for it", async () => {
    await completeSetup();
    org.id = "org-b";
    checks = { ...NOTHING_YET };

    rerenderView();

    await waitFor(() => {
      expect(screen.queryByText(ALL_SET_UP)).not.toBeInTheDocument();
      expect(screen.queryByText(SUMMARY_COUNT)).not.toBeInTheDocument();
      expectOpenStep("addKeys");
    });
    expect(tabInUrl()).toBe("addKeys");
  });

  it("when the person changes while the page is open, the page starts over for them", async () => {
    await completeSetup();
    auth.user = { id: "person-b", organization_role: "Owner" };
    checks = { ...KEYS_ONLY };

    rerenderView();

    await waitFor(() => {
      expect(screen.queryByText(ALL_SET_UP)).not.toBeInTheDocument();
      expect(screen.queryByText(SUMMARY_COUNT)).not.toBeInTheDocument();
      expectOpenStep("createFirstDataset");
    });
    expect(tabInUrl()).toBe("createFirstDataset");
  });

  // Guards (pass at the base too): right after sign-in the organization is
  // known a moment after the page opens.
  it("guard: the organization becoming known does not pin 'Add keys'", async () => {
    org.id = null;
    renderView("/dashboard/get-started");
    org.id = "org-a";
    rerenderView();

    await waitFor(() => expectOpenStep("createFirstDataset"));
    expect(tabInUrl()).toBe("createFirstDataset");
  });

  it("guard: the organization becoming known keeps the step in the address", async () => {
    org.id = null;
    checks = { ...NOTHING_YET };
    renderView("/dashboard/get-started?tab=RunFirstExperiment");
    org.id = "org-a";
    rerenderView();
    await waitFor(() => expect(checkUpdates()).toBeGreaterThan(0));
    await act(async () => {});
    await act(async () => {});

    expectOpenStep("RunFirstExperiment");
    expect(tabInUrl()).toBe("RunFirstExperiment");
  });
});

describe("Get started: the step opened on arrival is one the reader's list shows", () => {
  const OTHER_ROLES = [["Member"], ["Admin"], ["Viewer"]];
  // Roles that cannot invite anywhere: the invite step does not count for
  // them. It counts for an Admin, who can invite.
  const ROLES_THAT_CANNOT_INVITE = [["Member"], ["Viewer"]];
  const FIVE_LISTED_DONE = { ...ALL_DONE, invite: false };

  it.each(ROLES_THAT_CANNOT_INVITE)(
    "for the %s role, with the five listed steps done, opens a listed step, not the invite step",
    async (role) => {
      auth.user = { id: "person-m", organization_role: role };
      checks = { ...FIVE_LISTED_DONE };
      renderView("/dashboard/get-started");
      await checksLoaded();

      expect(LISTED_FOR_OTHER_ROLES).toContain(tabInUrl());
      expect(
        screen.queryByText(STEP_HEADER.inviteTeamMembers[1]),
      ).not.toBeInTheDocument();
      // As for an Owner who has done every step: the first step.
      expectOpenStep("addKeys");
      expect(tabInUrl()).toBe("addKeys");
    },
  );

  it.each(OTHER_ROLES)(
    "guard: for the %s role, with two listed steps done, opens the first unfinished listed step",
    async (role) => {
      auth.user = { id: "person-m", organization_role: role };
      checks = { ...KEYS_ONLY, dataset: true };
      renderView("/dashboard/get-started");
      await checksLoaded();

      expectOpenStep("createFirstEvaluation");
      expect(tabInUrl()).toBe("createFirstEvaluation");
    },
  );

  it("guard: an Owner with five of six steps done still opens the invite step", async () => {
    checks = { ...FIVE_LISTED_DONE };
    renderView("/dashboard/get-started");
    await checksLoaded();

    expectOpenStep("inviteTeamMembers");
    expect(tabInUrl()).toBe("inviteTeamMembers");
  });

  it("guard: an Owner with every step done still opens the first step", async () => {
    checks = { ...ALL_DONE };
    renderView("/dashboard/get-started");
    await checksLoaded();

    expectOpenStep("addKeys");
    expect(tabInUrl()).toBe("addKeys");
  });
});
