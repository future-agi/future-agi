import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

const { enqueueSnackbar } = vi.hoisted(() => ({ enqueueSnackbar: vi.fn() }));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar,
}));

const state = { review: null };
const decide = vi.fn();
const dismiss = vi.fn();
vi.mock("src/api/simulate-environments/draftReview", () => ({
  useDraftReview: vi.fn(() => ({ data: state.review, isLoading: false })),
  useDecideChanges: vi.fn(() => ({ mutate: decide, isPending: false })),
  useDismissFailedChange: vi.fn(() => dismiss),
}));
vi.mock("src/api/simulate-environments/versions", async (importOriginal) => ({
  ...(await importOriginal()),
  useEnvironmentVersions: vi.fn(() => ({
    data: {
      defaults: {
        environment: { label: "v1" },
        suite: { label: "v1" },
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
  render(
    <ReviewChangesDrawer
      open
      env={{ id: "env-1", versioning: { enabled: true } }}
      onClose={onClose}
      onSaved={onSaved}
      onOpenTab={onOpenTab}
      {...props}
    />,
  );
  return { onSaved, onClose, onOpenTab };
};

describe("ReviewChangesDrawer", () => {
  beforeEach(() => {
    decide.mockReset();
    dismiss.mockReset();
    enqueueSnackbar.mockReset();
  });

  it("undo sends a rejection for that change", async () => {
    const user = userEvent.setup();
    state.review = review([change()]);
    renderDrawer();
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(decide).toHaveBeenCalledWith([
      { id: "tool:refund", decision: "rejected" },
    ]);
  });

  it("an undone change leaves, and undoing all leaves nothing to save", () => {
    state.review = review([change({ decision: "rejected" })]);
    renderDrawer();
    expect(screen.queryByText("Change refund")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Save/ })).toBeNull();
    expect(screen.getByText(/Nothing waiting/)).toBeInTheDocument();
  });

  it("holds Undo while a kept change needs this one", () => {
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
    const [needed, free] = screen.getAllByRole("button", { name: "Undo" });
    expect(needed).toBeDisabled();
    expect(free).toBeEnabled();
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
    state.review = review([change()]);
    const { onSaved } = renderDrawer();
    await user.type(screen.getByLabelText(/Name this version/), "Q3 orders");
    await user.click(screen.getByRole("button", { name: /^Save/ }));
    expect(onSaved).toHaveBeenCalledWith({ name: "Q3 orders" });
    expect(enqueueSnackbar).toHaveBeenCalled();
  });

  it("dismisses a change that couldn't apply", async () => {
    const user = userEvent.setup();
    state.review = review(
      [],
      [
        {
          id: "failed:a",
          detail: "Missing table",
          reason: "Why",
          source_id: null,
          scenarios: [],
        },
      ],
    );
    renderDrawer();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(dismiss).toHaveBeenCalledWith("failed:a");
  });
});
