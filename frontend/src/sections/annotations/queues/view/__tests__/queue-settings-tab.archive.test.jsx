import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import QueueSettingsTab from "../queue-settings-tab";

// W10-ANNOTATIONS-U03: the Settings tab's Archive must ask first (like the
// queue list), warn about unsaved edits, offer Undo, and sit with the other
// removal action in the Danger zone instead of beside "Save Changes".

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  archive: vi.fn(),
  restore: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

// The tab imports useNavigate from "react-router".
vi.mock("react-router", async () => {
  const actual = await vi.importActual("react-router");
  return { ...actual, useNavigate: () => mocks.navigate };
});

vi.mock("notistack", async () => {
  const actual = await vi.importActual("notistack");
  return { ...actual, enqueueSnackbar: mocks.enqueueSnackbar };
});

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useArchiveAnnotationQueue: () => ({
    mutate: mocks.archive,
    isPending: false,
  }),
  useRestoreAnnotationQueue: () => ({
    mutate: mocks.restore,
    isPending: false,
  }),
  useHardDeleteAnnotationQueue: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateAnnotationQueue: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateAnnotationQueueStatus: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("../../components/label-picker", () => ({
  default: () => <div data-testid="label-picker" />,
}));

vi.mock("../../components/annotator-picker", () => ({
  default: () => <div data-testid="annotator-picker" />,
}));

const QUEUE = {
  id: "queue-1",
  name: "Hallucination QA",
  description: "",
  instructions: "Label each answer.",
  status: "active",
  assignment_strategy: "manual",
  annotations_required: 1,
  reservation_timeout_minutes: 60,
  requires_review: false,
  auto_assign: false,
  is_default: false,
  labels: [{ id: "label-1", label_id: "label-1", name: "Sentiment" }],
  annotators: [],
};

const renderTab = () =>
  render(
    <QueueSettingsTab queue={QUEUE} queueId="queue-1" creatorId="user-1" />,
  );

describe("QueueSettingsTab archive", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.archive.mockReset();
  });

  it("asks before archiving and stays on the page when the user cancels", async () => {
    const user = userEvent.setup();
    renderTab();

    await user.click(screen.getByRole("button", { name: /archive queue/i }));

    // Nothing happens until the user confirms.
    expect(mocks.archive).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Hallucination QA")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/You can restore it from Queues → Archived\./),
    ).toBeInTheDocument();
    // A clean form has nothing to lose.
    expect(within(dialog).queryByText(/Unsaved changes will be lost/)).toBe(
      null,
    );

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBe(null));

    expect(mocks.archive).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  it("warns that unsaved edits will be lost when the form has changes", async () => {
    const user = userEvent.setup();
    renderTab();

    await user.type(
      screen.getByRole("textbox", { name: /instructions for annotators/i }),
      " Be strict.",
    );
    await user.click(screen.getByRole("button", { name: /archive queue/i }));

    expect(mocks.archive).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();

    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText("Unsaved changes will be lost."),
    ).toBeInTheDocument();
  });

  it("archives on confirm, then returns to the queue list with an Undo toast", async () => {
    mocks.archive.mockImplementation((_id, options) => options?.onSuccess?.());
    const user = userEvent.setup();
    renderTab();

    await user.click(screen.getByRole("button", { name: /archive queue/i }));
    expect(mocks.archive).not.toHaveBeenCalled();

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Archive" }));

    expect(mocks.archive).toHaveBeenCalledWith("queue-1", expect.any(Object));
    expect(mocks.navigate).toHaveBeenCalledWith("/dashboard/annotations/queues");

    // Same toast as the queue list's Archive: it names the result and offers Undo.
    const undoToast = mocks.enqueueSnackbar.mock.calls.find(
      ([, options]) => typeof options?.action === "function",
    );
    expect(undoToast).toBeDefined();
    const [message, options] = undoToast;
    expect(message).toBe("Queue archived. Rules paused.");

    const { container } = render(<>{options.action("snack-1")}</>);
    await user.click(within(container).getByRole("button", { name: "Undo" }));
    expect(mocks.restore).toHaveBeenCalledWith("queue-1");
  });

  it("keeps Archive with Delete in the Danger zone, away from Save Changes", () => {
    renderTab();

    // The Danger zone no longer sends users to the queue list to archive.
    expect(screen.queryByText(/from the queue list instead/i)).toBe(null);

    const dangerZone = screen.getByRole("region", { name: "Danger zone" });
    expect(
      within(dangerZone).getByRole("button", { name: /archive queue/i }),
    ).toBeInTheDocument();
    expect(
      within(dangerZone).getByRole("button", {
        name: /delete queue permanently/i,
      }),
    ).toBeInTheDocument();
    // One Archive button on the tab: the one in the Danger zone.
    expect(screen.getAllByRole("button", { name: /archive queue/i })).toHaveLength(
      1,
    );
    expect(
      screen.getByRole("button", { name: /save changes/i }),
    ).toBeInTheDocument();
  });
});
