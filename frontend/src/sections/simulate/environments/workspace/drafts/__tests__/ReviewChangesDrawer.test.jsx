import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

const { enqueueSnackbar } = vi.hoisted(() => ({ enqueueSnackbar: vi.fn() }));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar,
}));

const decide = vi.fn();
vi.mock("src/api/simulate-environments/versions", async (importOriginal) => ({
  ...(await importOriginal()),
  useDecideChanges: vi.fn(() => ({ mutate: decide, isPending: false })),
  useEnvironmentVersions: vi.fn(() => ({
    data: {
      defaults: { environment: { label: "v1" }, suite: { label: "v2" } },
    },
  })),
}));

// What the shared save flow says, set per test.
const flow = {};
vi.mock("../useDraftSaveFlow", () => ({ default: vi.fn(() => flow) }));

const focusBuilderChat = vi.fn();
vi.mock("../../../buildEnvironment/console/builderChatFocusBus", () => ({
  focusBuilderChat: (...args) => focusBuilderChat(...args),
  subscribeBuilderChatFocus: () => () => {},
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

const setFlow = (changes, overrides = {}) => {
  Object.keys(flow).forEach((key) => delete flow[key]);
  Object.assign(flow, {
    open: true,
    review: { drafts: { environment: { id: "d1", revision: 3 } }, changes },
    reviewQuery: { isLoading: false, isError: false, refetch: vi.fn() },
    needsValidate: true,
    canSaveValidated: false,
    validating: false,
    pending: false,
    onValidate: vi.fn(),
    onSave: vi.fn(),
    onSaveValidated: vi.fn(),
    onDiscard: vi.fn(),
    ...overrides,
  });
};

const renderDrawer = (props = {}) => {
  const onSaved = vi.fn();
  const onClose = vi.fn();
  const { unmount } = render(
    <ReviewChangesDrawer
      open
      env={{ id: "env-1", versioning: { enabled: true } }}
      onClose={onClose}
      onSaved={onSaved}
      {...props}
    />,
  );
  return { onSaved, onClose, unmount };
};

beforeEach(() => {
  decide.mockReset();
  enqueueSnackbar.mockReset();
  focusBuilderChat.mockReset();
});

describe("ReviewChangesDrawer", () => {
  it("sends each decision with the revisions the person saw", async () => {
    const user = userEvent.setup();
    setFlow([change()]);
    renderDrawer();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(decide.mock.lastCall[0]).toEqual({
      expectedRevisions: { environment: 3 },
      decisions: [{ id: "tool:refund", decision: "accepted" }],
    });
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(decide.mock.lastCall[0].decisions).toEqual([
      { id: "tool:refund", decision: "rejected" },
    ]);
  });

  it("keeps a rejected change listed, to restore until the save", async () => {
    const user = userEvent.setup();
    setFlow([change({ decision: "rejected" })]);
    renderDrawer();
    await user.click(screen.getByRole("button", { name: "Restore" }));
    // The server only takes accepted or rejected, so Restore accepts it.
    expect(decide.mock.lastCall[0].decisions).toEqual([
      { id: "tool:refund", decision: "accepted" },
    ]);
  });

  it("lets an accepted change be rejected instead", async () => {
    const user = userEvent.setup();
    setFlow([change({ decision: "accepted" })]);
    renderDrawer();
    await user.click(screen.getByRole("button", { name: "Reject instead" }));
    expect(decide.mock.lastCall[0].decisions).toEqual([
      { id: "tool:refund", decision: "rejected" },
    ]);
  });

  it("holds Validate until every change is decided and one is accepted", async () => {
    const user = userEvent.setup();
    setFlow([
      change({ id: "tool:a", decision: "accepted" }),
      change({ id: "tool:b" }),
    ]);
    renderDrawer();
    const validate = screen.getByRole("button", { name: /^Validate/ });
    expect(validate).toHaveAttribute("aria-disabled", "true");
    await user.click(
      screen.getByRole("button", { name: "Accept all remaining" }),
    );
    expect(decide.mock.lastCall[0].decisions).toEqual([
      { remaining: true, decision: "accepted" },
    ]);
  });

  it("validates the decided changes before offering Save", async () => {
    const user = userEvent.setup();
    setFlow([change({ decision: "accepted" })]);
    renderDrawer();
    expect(screen.queryByLabelText(/Name this version/)).toBeNull();
    expect(
      screen.getByText("Validate the 1 scenario affected before you save."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Validate" }));
    expect(flow.onValidate).toHaveBeenCalled();
  });

  it("suggests a name from the first accepted change and saves with it", async () => {
    const user = userEvent.setup();
    setFlow(
      [change({ decision: "accepted", summary: "Refund window to 14 days" })],
      {
        canSaveValidated: true,
      },
    );
    renderDrawer();
    expect(screen.getByLabelText(/Name this version/)).toHaveValue(
      "Refund window to 14 days",
    );
    await user.click(screen.getByRole("button", { name: /^Save/ }));
    expect(flow.onSaveValidated).toHaveBeenCalledWith(
      "Refund window to 14 days",
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
    flow.onSaveValidated.mock.lastCall[1].onSuccess({
      // Kept as written has no reason: it isn't Active, but it runs.
      not_active: [
        { scenario_key: "a", reason: "not_validated" },
        { scenario_key: "b", reason: null },
      ],
    });
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "1 scenario won't run yet. Their reasons are on the Scenarios tab.",
      { variant: "warning" },
    );
  });

  it("saves without Validate when the server doesn't ask for it", async () => {
    const user = userEvent.setup();
    setFlow(
      [change({ decision: "accepted", summary: "Refund window to 14 days" })],
      { needsValidate: false },
    );
    renderDrawer();
    expect(screen.queryByRole("button", { name: "Validate" })).toBeNull();
    expect(screen.getByLabelText(/Name this version/)).toHaveValue(
      "Refund window to 14 days",
    );
    await user.click(screen.getByRole("button", { name: /^Save/ }));
    expect(flow.onSave).toHaveBeenCalledWith(
      "Refund window to 14 days",
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
    expect(flow.onSaveValidated).not.toHaveBeenCalled();
  });

  it.each([true, false])(
    "holds Save, not Validate, while scenarios are validated (needs Validate: %s)",
    (needsValidate) => {
      setFlow([change({ decision: "accepted" })], {
        needsValidate,
        canSaveValidated: needsValidate,
        scenariosValidating: true,
      });
      const { unmount } = renderDrawer();
      expect(
        screen.getByRole("button", {
          name: /^Save.*Save waits until it finishes\./,
        }),
      ).toHaveAttribute("aria-disabled", "true");
      unmount();
      setFlow([change({ decision: "accepted" })], {
        scenariosValidating: true,
      });
      renderDrawer();
      expect(
        screen.getByRole("button", { name: "Validate" }),
      ).not.toHaveAttribute("aria-disabled");
    },
  );

  it("holds a validated Save until the version has a name", async () => {
    const user = userEvent.setup();
    setFlow([change({ decision: "accepted", summary: "Refund window" })], {
      canSaveValidated: true,
    });
    renderDrawer();
    const field = screen.getByLabelText("Name this version");
    expect(screen.getByRole("button", { name: /^Save/ })).not.toHaveAttribute(
      "aria-disabled",
    );
    await user.clear(field);
    expect(screen.getByRole("button", { name: /^Save/ })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("holds Save for a name over the limit", async () => {
    const user = userEvent.setup();
    setFlow([change({ decision: "accepted", summary: "" })], {
      canSaveValidated: true,
    });
    renderDrawer();
    const field = screen.getByLabelText(/Name this version/);
    await user.click(field);
    await user.paste("x".repeat(81));
    expect(field).toBeInvalid();
    expect(screen.getByRole("button", { name: /^Save/ })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("names the scenarios a failed validation needs changed", () => {
    setFlow([change({ decision: "accepted" })], {
      validationState: "failed",
      validationNote: "Validation failed. 2 scenarios need a change",
      failedScenarios: ["refund-a", "refund-b"],
    });
    renderDrawer();
    expect(
      screen.getByText(
        "Validation failed. 2 scenarios need a change: refund-a, refund-b",
      ),
    ).toBeInTheDocument();
  });

  it("offers a retry when the review won't load, not an empty list", async () => {
    const user = userEvent.setup();
    setFlow([], {
      review: undefined,
      reviewQuery: { isError: true, refetch: vi.fn() },
    });
    renderDrawer();
    expect(screen.queryByText(/Nothing waiting/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(flow.reviewQuery.refetch).toHaveBeenCalled();
  });

  it("holds Reject while a kept change needs this one", () => {
    setFlow([
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

  it.each([
    ["at the message it answered", [{ kind: "message", id: "m2" }], "m2"],
    ["when no message is named", undefined, undefined],
  ])("opens the chat beside it %s", async (_, used, messageId) => {
    const user = userEvent.setup();
    setFlow([change({ sources: [{ origin: "chat", used }] })]);
    const { onClose } = renderDrawer();
    await user.click(screen.getByRole("button", { name: "Open in chat" }));
    expect(focusBuilderChat).toHaveBeenCalledWith(messageId);
    expect(onClose).not.toHaveBeenCalled();
  });

  it.each([
    [
      "its name and row",
      { name: "o.csv", row: 7 },
      "From o.csv · row 7",
      "Open o.csv · row 7",
    ],
    ["no name yet", { row: null }, "From an imported file", "Open"],
  ])("shows a file source with %s", (_, file, line, link) => {
    const used = [{ kind: "file", id: "f-1", ...file }];
    setFlow([change({ sources: [{ origin: "manual", used }] })]);
    renderDrawer();
    expect(screen.getByText(line)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: link })).toBeInTheDocument();
  });

  it("holds Validate and Discard while the builder is working", () => {
    setFlow([change({ decision: "accepted" })]);
    renderDrawer({ busy: true });
    expect(screen.getByRole("button", { name: /^Validate/ })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByRole("button", { name: "Discard all" })).toBeDisabled();
  });
});
