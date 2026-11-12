import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import ErrorMetadataPanel from "../ErrorMetadataPanel";

// The issue sidebar's Triage controls. Status, Severity and Assignee are
// buttons a keyboard user can Tab to and open with Enter or Space. Severity
// reads the issue's own severity the way the header badge does: a
// compatibility default shows as Pending or Unassessed, Medium stays
// selectable, and a severity graded while the page is open shows up. A
// severity picked here shows at once while its save is pending, and gives
// way to the issue's own severity when that changes.
//
// The save mock never settles, so every pick here stays pending. No fixture
// here passes an icon: every glyph these controls draw belongs to
// ErrorMetadataPanel.jsx.

const mocks = vi.hoisted(() => ({ mutate: vi.fn() }));

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
  useUpdateErrorFeedIssue: () => ({ mutate: mocks.mutate, isPending: false }),
  useLinearTeams: () => ({
    data: { connected: false, teams: [] },
    isLoading: false,
    isError: false,
  }),
  useCreateLinearIssue: () => ({ mutate: vi.fn(), isPending: false }),
}));

const assessed = {
  cluster_id: "E-1",
  status: "escalating",
  severity: "high",
  severity_source: "llm",
  severity_assessment_status: "completed",
  assignees: ["ana@acme.test"],
  trace_count: 8,
  first_seen: "2026-10-21T12:00:00Z",
};

const defaulted = (assessmentStatus) => ({
  ...assessed,
  severity: "medium",
  severity_source: "default",
  severity_assessment_status: assessmentStatus,
});

const severityRow = () => screen.getByText("Severity").parentElement;

const menuItemLabels = (menu) =>
  within(menu)
    .getAllByRole("menuitem")
    .map((item) => item.textContent);

const pickSeverity = async (user, from, to) => {
  await user.click(within(severityRow()).getByText(from));
  const menu = await screen.findByRole("menu");
  await user.click(within(menu).getByRole("menuitem", { name: to }));
};

beforeEach(() => {
  mocks.mutate.mockClear();
});

describe("Issue triage controls from the keyboard", () => {
  it("puts Status, Severity and Assignee in the Tab order as menu buttons", async () => {
    const user = userEvent.setup();
    render(<ErrorMetadataPanel error={assessed} />);

    for (const name of [
      "Status: Escalating",
      "Severity: High",
      "Assignee: Ana Ruiz",
    ]) {
      await user.tab();
      const control = screen.getByRole("button", { name });
      expect(control).toHaveFocus();
      expect(control).toHaveAttribute("aria-haspopup", "menu");
      expect(control).toHaveAttribute("aria-expanded", "false");
    }
  });

  it("opens Severity with Enter and sets the severity picked", async () => {
    const user = userEvent.setup();
    render(<ErrorMetadataPanel error={assessed} />);

    await user.tab();
    await user.tab();
    expect(
      screen.getByRole("button", { name: "Severity: High" }),
    ).toHaveFocus();
    await user.keyboard("{Enter}");

    const menu = await screen.findByRole("menu");
    expect(
      screen.getByRole("button", { name: "Severity: High", hidden: true }),
    ).toHaveAttribute("aria-expanded", "true");
    await user.click(within(menu).getByRole("menuitem", { name: "Critical" }));
    // The save may carry its options as a second argument.
    expect(mocks.mutate).toHaveBeenCalledTimes(1);
    expect(mocks.mutate.mock.calls[0][0]).toEqual({
      clusterId: "E-1",
      severity: "critical",
    });
  });

  it("opens Status with Space and moves the issue to For review", async () => {
    const user = userEvent.setup();
    render(<ErrorMetadataPanel error={assessed} />);

    await user.tab();
    expect(
      screen.getByRole("button", { name: "Status: Escalating" }),
    ).toHaveFocus();
    await user.keyboard("[Space]");

    const menu = await screen.findByRole("menu");
    await user.click(
      within(menu).getByRole("menuitem", { name: "For review" }),
    );
    expect(mocks.mutate).toHaveBeenCalledWith({
      clusterId: "E-1",
      status: "for_review",
    });
  });

  it("opens Assignee with Enter and unassigns", async () => {
    const user = userEvent.setup();
    render(<ErrorMetadataPanel error={assessed} />);

    await user.tab();
    await user.tab();
    await user.tab();
    expect(
      screen.getByRole("button", { name: "Assignee: Ana Ruiz" }),
    ).toHaveFocus();
    await user.keyboard("{Enter}");

    const menu = await screen.findByRole("menu");
    await user.click(within(menu).getByRole("menuitem", { name: "Unassign" }));
    expect(mocks.mutate).toHaveBeenCalledWith(
      { clusterId: "E-1", assignee: null },
      expect.anything(),
    );
  });
});

describe("Sidebar severity before it is assessed", () => {
  it.each([
    ["pending", "Pending"],
    ["insufficient_evidence", "Unassessed"],
    ["failed", "Unassessed"],
  ])(
    "shows a default severity with status %s as %s, like the header",
    (assessmentStatus, label) => {
      render(<ErrorMetadataPanel error={defaulted(assessmentStatus)} />);

      expect(severityRow()).toHaveTextContent(label);
      expect(severityRow()).not.toHaveTextContent("Medium");
    },
  );

  it("offers all four severities, Medium included, and sets the one picked", async () => {
    const user = userEvent.setup();
    render(<ErrorMetadataPanel error={defaulted("pending")} />);

    await user.click(within(severityRow()).getByText("Pending"));

    const menu = await screen.findByRole("menu");
    expect(menuItemLabels(menu)).toEqual(["Critical", "High", "Medium", "Low"]);
    await user.click(within(menu).getByRole("menuitem", { name: "Medium" }));
    // The save may carry its options as a second argument.
    expect(mocks.mutate).toHaveBeenCalledTimes(1);
    expect(mocks.mutate.mock.calls[0][0]).toEqual({
      clusterId: "E-1",
      severity: "medium",
    });
  });

  it("follows the issue when its severity changes while the page is open", () => {
    const { rerender } = render(
      <ErrorMetadataPanel error={{ ...assessed, severity: "low" }} />,
    );
    expect(severityRow()).toHaveTextContent("Low");

    rerender(
      <ErrorMetadataPanel
        error={{
          ...assessed,
          severity: "critical",
          severity_source: "manual",
          severity_assessment_status: "manual",
        }}
      />,
    );

    expect(severityRow()).toHaveTextContent("Critical");
    expect(severityRow()).not.toHaveTextContent("Low");
  });

  it("keeps a legacy severity as its label and leaves it out of the menu", async () => {
    const user = userEvent.setup();
    render(
      <ErrorMetadataPanel
        error={{
          ...assessed,
          severity: "medium",
          severity_source: "legacy",
          severity_assessment_status: "unassessed",
        }}
      />,
    );

    expect(severityRow()).toHaveTextContent("Medium");
    await user.click(within(severityRow()).getByText("Medium"));

    const menu = await screen.findByRole("menu");
    expect(menuItemLabels(menu)).toEqual(["Critical", "High", "Low"]);
  });
});

describe("Sidebar severity while a pick is saving", () => {
  it("shows a pick at once while its save is pending", async () => {
    const user = userEvent.setup();
    render(<ErrorMetadataPanel error={assessed} />);

    await pickSeverity(user, "High", "Critical");

    expect(severityRow()).toHaveTextContent("Critical");
    expect(severityRow()).not.toHaveTextContent("High");
    expect(mocks.mutate).toHaveBeenCalledWith(
      { clusterId: "E-1", severity: "critical" },
      expect.objectContaining({ onSettled: expect.any(Function) }),
    );
  });

  it("shows the issue's severity when it changes while a pick is pending", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<ErrorMetadataPanel error={assessed} />);

    await pickSeverity(user, "High", "Critical");
    expect(mocks.mutate).toHaveBeenCalledTimes(1);

    rerender(<ErrorMetadataPanel error={{ ...assessed, severity: "low" }} />);

    expect(severityRow()).toHaveTextContent("Low");
    expect(severityRow()).not.toHaveTextContent("Critical");
  });
});
