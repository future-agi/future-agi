import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import ErrorMetadataPanel from "../ErrorMetadataPanel";

// The issue's Assignee picker in a workspace with more members than one page
// of the members list. Anyone can be found by searching or by loading more,
// an assignee outside the loaded page still shows as the issue's owner, and
// a refused save puts the previous assignee back.

const PAGE_ONE = [
  { email: "ana@acme.test", name: "Ana Ruiz" },
  { email: "ben@acme.test", name: "Ben Ode" },
];
const ZED = { email: "zed@acme.test", name: "Zed Park" };

const h = vi.hoisted(() => ({
  mutate: vi.fn(),
  fetchNextPage: vi.fn(),
  members: vi.fn(),
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-1" }),
}));
vi.mock(
  "src/api/annotation-queues/annotation-queues",
  async (importOriginal) => ({
    ...(await importOriginal()),
    // Both member readers answer alike: what differs is only the picker.
    useOrgMembers: (orgId) => h.members(orgId, ""),
    useOrgMembersInfinite: (orgId, search = "") => h.members(orgId, search),
  }),
);
vi.mock("src/api/shared-links", async (importOriginal) => ({
  ...(await importOriginal()),
  useSharedIssueSidebar: () => ({ data: undefined, isLoading: false }),
}));
vi.mock("src/api/errorFeed/error-feed", async (importOriginal) => ({
  ...(await importOriginal()),
  useErrorFeedSidebar: () => ({ data: undefined, isLoading: false }),
  useUpdateErrorFeedIssue: () => ({ mutate: h.mutate, isPending: false }),
  useLinearTeams: () => ({
    data: { connected: false, teams: [] },
    isLoading: false,
    isError: false,
  }),
  useCreateLinearIssue: () => ({ mutate: vi.fn(), isPending: false }),
}));

const issue = (assignee) => ({
  cluster_id: "E-1",
  status: "escalating",
  severity: "high",
  severity_source: "llm",
  severity_assessment_status: "completed",
  assignees: assignee ? [assignee] : [],
  trace_count: 8,
  first_seen: "2026-10-21T12:00:00Z",
});

// The first page only; Zed sorts later and is reached by searching.
const firstPageThenSearch = (_orgId, search) => ({
  data: search ? [ZED].filter((m) => m.email.includes(search)) : PAGE_ONE,
  hasNextPage: !search,
  fetchNextPage: h.fetchNextPage,
  isFetching: false,
  isFetchingNextPage: false,
});

const openAssignee = async (user, name) => {
  await user.click(screen.getByRole("button", { name: `Assignee: ${name}` }));
  return screen.findByRole("menu");
};

beforeEach(() => {
  h.mutate.mockReset();
  h.fetchNextPage.mockReset();
  h.members.mockReset().mockImplementation(firstPageThenSearch);
});

describe("Assignee picker past the first page of members", () => {
  it("shows an assignee outside the loaded page by email, not as unassigned", () => {
    render(<ErrorMetadataPanel error={issue(ZED.email)} />);

    const button = screen.getByRole("button", {
      name: `Assignee: ${ZED.email}`,
    });
    expect(button).toHaveTextContent("zed");
    expect(button).not.toHaveTextContent("Assign");
  });

  it("finds a later member by searching and assigns them", async () => {
    const user = userEvent.setup();
    render(<ErrorMetadataPanel error={issue("ana@acme.test")} />);

    const menu = await openAssignee(user, "Ana Ruiz");
    await user.type(
      within(menu).getByRole("textbox", { name: "Search members" }),
      "zed",
    );
    await waitFor(() => expect(h.members).toHaveBeenCalledWith("org-1", "zed"));
    await user.click(
      await within(menu).findByRole("menuitem", { name: /Zed Park/ }),
    );

    expect(h.mutate).toHaveBeenCalledWith(
      { clusterId: "E-1", assignee: ZED.email },
      expect.anything(),
    );
  });

  it("loads the next page from Show more members", async () => {
    const user = userEvent.setup();
    render(<ErrorMetadataPanel error={issue("ana@acme.test")} />);

    const menu = await openAssignee(user, "Ana Ruiz");
    await user.click(
      within(menu).getByRole("menuitem", { name: "Show more members" }),
    );

    expect(h.fetchNextPage).toHaveBeenCalledTimes(1);
  });

  it("puts the previous assignee back when the save is refused", async () => {
    h.mutate.mockImplementation((_variables, options) =>
      options?.onError?.(new Error("Assignee must be a workspace member")),
    );
    const user = userEvent.setup();
    render(<ErrorMetadataPanel error={issue("ana@acme.test")} />);

    const menu = await openAssignee(user, "Ana Ruiz");
    await user.click(within(menu).getByRole("menuitem", { name: /Ben Ode/ }));

    expect(h.mutate).toHaveBeenCalledWith(
      { clusterId: "E-1", assignee: "ben@acme.test" },
      expect.anything(),
    );
    expect(
      await screen.findByRole("button", {
        name: "Assignee: Ana Ruiz",
        hidden: true,
      }),
    ).toBeInTheDocument();
  });
});
