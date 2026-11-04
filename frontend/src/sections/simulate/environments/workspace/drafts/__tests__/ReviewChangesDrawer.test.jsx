import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

const { enqueueSnackbar } = vi.hoisted(() => ({ enqueueSnackbar: vi.fn() }));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar,
}));

const state = { review: null, query: {} };
const decide = vi.fn();
const dismiss = vi.fn();
vi.mock("src/api/simulate-environments/draftReview", () => ({
  useDraftReview: vi.fn(() => ({
    data: state.review,
    isLoading: false,
    ...state.query,
  })),
  useDecideChanges: vi.fn(() => ({ mutate: decide, isPending: false })),
  useDismissFailedChange: vi.fn(() => dismiss),
}));
vi.mock("src/api/simulate-environments/versions", async (importOriginal) => ({
  ...(await importOriginal()),
  useEnvironmentVersions: vi.fn(() => ({
    data: {
      defaults: {
        environment: { label: "v1" },
        suite: { label: "v2" },
      },
    },
  })),
}));

const { default: ReviewChangesDrawer } = await import("../ReviewChangesDrawer");

const change = (overrides) => ({
  id: "tool:refund",
  piece: "environment",
  kind: "tool",
  key: "refund",
  action: "changed",
  patch: [{ op: "add", path: "/note", value: "new" }],
  summary: "Change refund",
  decision: null,
  sources: [],
  depends_on: [],
  graded: true,
  revalidate: ["s1"],
  ...overrides,
});
const review = (changes, failed = []) => ({
  is_sample: true,
  sample_scenario_total: 2,
  changes,
  failed,
});
const renderDrawer = (props = {}) => {
  const onSaved = vi.fn();
  const onClose = vi.fn();
  const onOpenTab = vi.fn();
  const drawer = () => (
    <ReviewChangesDrawer
      open
      env={{ id: "env-1", versioning: { enabled: true } }}
      onClose={onClose}
      onSaved={onSaved}
      onOpenTab={onOpenTab}
      {...props}
    />
  );
  const { rerender } = render(drawer());
  return {
    onSaved,
    onClose,
    onOpenTab,
    rerenderDrawer: () => rerender(drawer()),
  };
};

describe("ReviewChangesDrawer", () => {
  beforeEach(() => {
    decide.mockReset();
    dismiss.mockReset();
    enqueueSnackbar.mockReset();
    state.query = {};
  });

  it("sends each decision for that change", async () => {
    const user = userEvent.setup();
    state.review = review([change()]);
    renderDrawer();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(decide).toHaveBeenLastCalledWith([
      { id: "tool:refund", decision: "accepted" },
    ]);
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(decide).toHaveBeenLastCalledWith([
      { id: "tool:refund", decision: "rejected" },
    ]);
  });

  it("keeps a rejected change listed, to restore until the save", async () => {
    const user = userEvent.setup();
    state.review = review([change({ decision: "rejected" })]);
    renderDrawer();
    expect(screen.getByText("Change refund")).toBeInTheDocument();
    expect(screen.queryByText(/Nothing waiting/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Restore" }));
    expect(decide).toHaveBeenCalledWith([
      { id: "tool:refund", decision: null },
    ]);
  });

  it("lets an accepted change go back to undecided", async () => {
    const user = userEvent.setup();
    state.review = review([change({ decision: "accepted" })]);
    renderDrawer();
    await user.click(screen.getByRole("button", { name: "Change" }));
    expect(decide).toHaveBeenCalledWith([
      { id: "tool:refund", decision: null },
    ]);
  });

  it("holds Save until every change is decided, and one is accepted", async () => {
    const user = userEvent.setup();
    const save = () => screen.getByRole("button", { name: /^Save/ });
    state.review = review([
      change({ id: "tool:a", decision: "accepted" }),
      change({ id: "tool:b" }),
    ]);
    const { rerenderDrawer } = renderDrawer();
    expect(save()).toHaveAttribute("aria-disabled", "true");
    await user.click(
      screen.getByRole("button", { name: "Accept all remaining" }),
    );
    expect(decide).toHaveBeenCalledWith([
      { remaining: true, decision: "accepted" },
    ]);

    state.review = review([
      change({ id: "tool:a", decision: "accepted" }),
      change({ id: "tool:b", decision: "rejected" }),
    ]);
    rerenderDrawer();
    expect(save()).not.toHaveAttribute("aria-disabled");

    state.review = review([change({ decision: "rejected" })]);
    rerenderDrawer();
    expect(save()).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("button", { name: "Discard all" })).toBeEnabled();
  });

  it("holds Reject while a kept change needs this one", () => {
    state.review = review([
      change({ id: "actor:a", kind: "actor", summary: "Added a" }),
      change({
        id: "scenario:s",
        kind: "scenario",
        summary: "Change s",
        depends_on: ["actor:a"],
      }),
    ]);
    renderDrawer();
    const [needed, free] = screen.getAllByRole("button", { name: /^Reject/ });
    expect(needed).toHaveAttribute("aria-disabled", "true");
    expect(free).not.toHaveAttribute("aria-disabled");
  });

  it("keeps a held Reject reachable by keyboard, and it does nothing", async () => {
    const user = userEvent.setup();
    state.review = review([
      change({ id: "actor:a", kind: "actor", summary: "Added a" }),
      change({
        id: "scenario:s",
        kind: "scenario",
        summary: "Change s",
        depends_on: ["actor:a"],
      }),
    ]);
    renderDrawer();
    const held = screen.getByRole("button", { name: /^Reject\. .*Change s/ });
    act(() => held.focus());
    expect(held).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(decide).not.toHaveBeenCalled();
  });

  it("moves focus to the outcome when a decision lands", async () => {
    const user = userEvent.setup();
    state.review = review([change()]);
    const { rerenderDrawer } = renderDrawer();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    state.review = review([change({ decision: "accepted" })]);
    rerenderDrawer();
    expect(screen.getByRole("button", { name: "Change" })).toHaveFocus();
  });

  it("names the version of what the edits change", async () => {
    const user = userEvent.setup();
    state.review = review([change({ piece: "suite", kind: "scenario" })]);
    renderDrawer();
    expect(screen.getByText(/since scenarios v2/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Discard all" }));
    expect(screen.getByText(/back to scenarios v2/)).toBeInTheDocument();
  });

  it("offers a retry when the review won't load, not an empty list", async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    state.review = undefined;
    state.query = { isError: true, refetch };
    renderDrawer();
    expect(screen.queryByText(/Nothing waiting/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("opens a row to its diff", async () => {
    const user = userEvent.setup();
    state.review = review([change()]);
    renderDrawer();
    expect(screen.queryByText(/note: new/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show the change" }));
    expect(screen.getByText(/note: new/)).toBeInTheDocument();
  });

  it("disables the chevron of a change with no diff", () => {
    state.review = review([change({ kind: "file", patch: [] })]);
    renderDrawer();
    expect(
      screen.getByRole("button", { name: "Show the change" }),
    ).toBeDisabled();
  });

  it("hands over the version name, without touching the real draft", async () => {
    const user = userEvent.setup();
    state.review = review([change({ decision: "accepted" })]);
    const { onSaved } = renderDrawer();
    await user.type(screen.getByLabelText(/Name this version/), "Q3 orders");
    await user.click(screen.getByRole("button", { name: /^Save/ }));
    expect(onSaved).toHaveBeenCalledWith({ name: "Q3 orders" });
    expect(enqueueSnackbar).toHaveBeenCalled();
  });

  it("flags a name over the limit and holds Save", async () => {
    const user = userEvent.setup();
    state.review = review([change({ decision: "accepted" })]);
    renderDrawer();
    const field = screen.getByLabelText(/Name this version/);
    await user.click(field);
    await user.paste("x".repeat(80));
    expect(field).toBeValid();
    const save = () => screen.getByRole("button", { name: /^Save/ });
    expect(save()).not.toHaveAttribute("aria-disabled");
    await user.keyboard("x");
    expect(field).toBeInvalid();
    expect(save()).toHaveAttribute("aria-disabled", "true");
  });

  it("offers a read-only viewer no way to act on a failed change", () => {
    state.review = review(
      [],
      [
        {
          id: "failed:a",
          detail: "Missing table",
          reason: "Why",
          source_id: "a.csv",
          scenarios: [],
        },
      ],
    );
    renderDrawer({ canEdit: false });
    expect(screen.getByText("Missing table")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Fix/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
  });

  it("sets a failed change aside, and brings it back", async () => {
    const user = userEvent.setup();
    const failed = {
      id: "failed:a",
      detail: "Missing table",
      reason: "Why",
      source_id: null,
      scenarios: [],
    };
    state.review = review([], [failed]);
    const { rerenderDrawer } = renderDrawer();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(dismiss).toHaveBeenCalledWith("failed:a");

    state.review = review([change()], [{ ...failed, dismissed: true }]);
    rerenderDrawer();
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show" }));
    await user.click(screen.getByRole("button", { name: "Bring back" }));
    expect(dismiss).toHaveBeenCalledWith("failed:a", false);
  });
});
