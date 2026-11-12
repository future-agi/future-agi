import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import ErrorMetadataPanel from "../ErrorMetadataPanel";

// The issue sidebar's Status and Severity menus from the keyboard. Enter
// opens a menu on its first option, ArrowDown and ArrowUp move between the
// options, Enter applies the focused one and Escape closes the menu and gives
// focus back to its button. The headings stay in the menus, and Assignee
// keeps working as it did.
//
// No fixture here passes an icon: every glyph these menus draw belongs to
// ErrorMetadataPanel.jsx, and focus is checked by role and text, not by
// accessible name.

const h = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/contexts/OrganizationContext", async (importOriginal) => ({
  ...(await importOriginal()),
  useOrganization: () => ({ currentOrganizationId: "org-1" }),
}));
vi.mock(
  "src/api/annotation-queues/annotation-queues",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useOrgMembersInfinite: () => ({
      data: [
        { email: "ana@acme.test", name: "Ana Ruiz" },
        { email: "ben@acme.test", name: "Ben Ode" },
      ],
      hasNextPage: false,
      fetchNextPage: vi.fn(),
      isFetching: false,
      isFetchingNextPage: false,
    }),
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

const issue = {
  cluster_id: "E-1",
  status: "escalating",
  severity: "high",
  severity_source: "llm",
  severity_assessment_status: "completed",
  assignees: [],
  trace_count: 8,
  first_seen: "2026-10-21T12:00:00Z",
};

const focused = () => document.activeElement;

// Status, Severity and Assignee are the panel's first three Tab stops.
const tabTo = async (user, stops, name) => {
  for (let i = 0; i < stops; i += 1) {
    await user.tab();
  }
  const button = screen.getByRole("button", { name });
  expect(button).toHaveFocus();
  return button;
};

beforeEach(() => {
  h.mutate.mockReset();
});

describe("Issue Status and Severity menus from the keyboard", () => {
  it("opens Status on its first option, moves with ArrowDown and ArrowUp, and applies the focused status with Enter", async () => {
    const user = userEvent.setup();
    render(<ErrorMetadataPanel error={issue} />);

    await tabTo(user, 1, "Status: Escalating");
    await user.keyboard("{Enter}");

    const menu = await screen.findByRole("menu");
    expect(within(menu).getByText("Change status")).toBeInTheDocument();
    expect(within(menu).getByRole("separator")).toBeInTheDocument();
    await waitFor(() => expect(focused()).toHaveAttribute("role", "menuitem"));
    expect(focused()).toHaveTextContent("Acknowledged");

    await user.keyboard("{ArrowDown}");
    expect(focused()).toHaveAttribute("role", "menuitem");
    expect(focused()).toHaveTextContent("For review");
    await user.keyboard("{ArrowDown}");
    expect(focused()).toHaveTextContent("Resolved");
    await user.keyboard("{ArrowUp}");
    expect(focused()).toHaveTextContent("For review");
    expect(h.mutate).not.toHaveBeenCalled();

    await user.keyboard("{Enter}");
    expect(h.mutate).toHaveBeenCalledTimes(1);
    expect(h.mutate).toHaveBeenCalledWith({
      clusterId: "E-1",
      status: "for_review",
    });
  });

  it("opens Severity on its first option, moves with ArrowDown, and applies the focused severity with Enter", async () => {
    const user = userEvent.setup();
    render(<ErrorMetadataPanel error={issue} />);

    await tabTo(user, 2, "Severity: High");
    await user.keyboard("{Enter}");

    const menu = await screen.findByRole("menu");
    expect(within(menu).getByText("Change severity")).toBeInTheDocument();
    await waitFor(() => expect(focused()).toHaveAttribute("role", "menuitem"));
    expect(focused()).toHaveTextContent("Critical");

    await user.keyboard("{ArrowDown}");
    expect(focused()).toHaveAttribute("role", "menuitem");
    expect(focused()).toHaveTextContent("Medium");
    expect(h.mutate).not.toHaveBeenCalled();

    await user.keyboard("{Enter}");
    expect(h.mutate).toHaveBeenCalledTimes(1);
    // The save's variables are exact; a second argument carries per-call
    // callbacks.
    expect(h.mutate.mock.calls[0][0]).toEqual({
      clusterId: "E-1",
      severity: "medium",
    });
  });

  it("closes Status with Escape and gives focus back to the Status button", async () => {
    const user = userEvent.setup();
    render(<ErrorMetadataPanel error={issue} />);

    const status = await tabTo(user, 1, "Status: Escalating");
    await user.keyboard("{Enter}");
    await screen.findByRole("menu");
    await waitFor(() => expect(focused()).toHaveAttribute("role", "menuitem"));
    await user.keyboard("{ArrowDown}");
    expect(focused()).toHaveAttribute("role", "menuitem");
    expect(focused()).toHaveTextContent("For review");

    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("menu")).not.toBeInTheDocument(),
    );
    await waitFor(() => expect(status).toHaveFocus());
    expect(status).toHaveAttribute("aria-expanded", "false");
    expect(h.mutate).not.toHaveBeenCalled();
  });

  it("still moves from the Assignee search field to the first member with ArrowDown and assigns them with Enter", async () => {
    const user = userEvent.setup();
    render(<ErrorMetadataPanel error={issue} />);

    await tabTo(user, 3, "Assignee: Unassigned");
    await user.keyboard("{Enter}");

    const menu = await screen.findByRole("menu");
    await waitFor(() =>
      expect(
        within(menu).getByRole("textbox", { name: "Search members" }),
      ).toHaveFocus(),
    );
    await user.keyboard("{ArrowDown}");
    expect(focused()).toHaveAttribute("role", "menuitem");
    expect(focused()).toHaveTextContent("Ana Ruiz");

    await user.keyboard("{Enter}");
    expect(h.mutate).toHaveBeenCalledWith(
      { clusterId: "E-1", assignee: "ana@acme.test" },
      expect.anything(),
    );
  });
});
