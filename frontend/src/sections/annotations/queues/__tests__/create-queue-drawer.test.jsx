import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import CreateQueueDrawer from "../create-queue-drawer";

// Auto-invoke onSuccess so the drawer's chained status update can fire.
const mockCreateQueue = vi.fn((_payload, opts) => opts?.onSuccess?.());
const mockUpdateQueue = vi.fn((_payload, opts) => opts?.onSuccess?.());
const mockUpdateStatus = vi.fn((_payload, opts) => opts?.onSuccess?.());

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useCreateAnnotationQueue: () => ({
    mutate: mockCreateQueue,
    isPending: false,
  }),
  useUpdateAnnotationQueue: () => ({
    mutate: mockUpdateQueue,
    isPending: false,
  }),
  useUpdateAnnotationQueueStatus: () => ({
    mutate: mockUpdateStatus,
    isPending: false,
  }),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

// Return a STABLE user object. The drawer's reset effect lists `user` in its
// deps; a fresh object each render would re-run the effect → reset() → re-render
// in an infinite loop (the real auth context is memoized, so it stays stable).
vi.mock("src/auth/hooks", () => {
  const user = { id: "user-1" };
  return { useAuthContext: () => ({ user }) };
});

vi.mock("../components/label-picker", () => ({
  default: () => <div data-testid="label-picker" />,
}));

vi.mock("../components/annotator-picker", () => ({
  default: () => <div data-testid="annotator-picker" />,
}));

const ACTIVE_QUEUE = {
  id: "queue-1",
  name: "Hallucination QA",
  description: "",
  status: "active",
  annotations_required: 1,
  annotators: [],
  labels: [{ id: "label-1", name: "Sentiment" }],
};

describe("CreateQueueDrawer status update", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("routes a changed status through the dedicated update-status endpoint", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <CreateQueueDrawer open onClose={onClose} editQueue={ACTIVE_QUEUE} />,
    );

    // active -> paused is a valid transition; pick it from the Status select.
    await user.click(screen.getByRole("combobox", { name: /status/i }));
    await user.click(await screen.findByRole("option", { name: "Paused" }));

    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      // Status must NOT ride the general queue update (it's read-only there and
      // is silently dropped — the bug). It goes through update-status instead.
      expect(mockUpdateStatus).toHaveBeenCalledWith(
        expect.objectContaining({ id: "queue-1", status: "paused" }),
        expect.any(Object),
      );
    });
    expect(mockUpdateQueue).toHaveBeenCalledWith(
      expect.not.objectContaining({ status: expect.anything() }),
      expect.any(Object),
    );
  });

  it("does not call update-status when the status is unchanged", async () => {
    const user = userEvent.setup();
    render(
      <CreateQueueDrawer open onClose={vi.fn()} editQueue={ACTIVE_QUEUE} />,
    );

    // Save without touching the Status select. A same-status transition is
    // rejected by the state machine, so it must be skipped entirely.
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(mockUpdateQueue).toHaveBeenCalledOnce();
    });
    expect(mockUpdateStatus).not.toHaveBeenCalled();
  });

  it("does not offer an unreachable status target (no transition leads to draft)", async () => {
    const user = userEvent.setup();
    render(
      <CreateQueueDrawer open onClose={vi.fn()} editQueue={ACTIVE_QUEUE} />,
    );

    await user.click(screen.getByRole("combobox", { name: /status/i }));

    expect(screen.getByRole("option", { name: "Paused" })).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Completed" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Draft" })).toBeNull();
  });
});

describe("CreateQueueDrawer label requirement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("blocks create and shows an inline error when no label is selected", async () => {
    const user = userEvent.setup();
    render(<CreateQueueDrawer open onClose={vi.fn()} />);

    await user.type(screen.getByLabelText(/queue name/i), "Hallucination QA");
    await user.click(
      screen.getByRole("button", { name: /create annotation queue/i }),
    );

    expect(
      await screen.findByText(/at least one label is required/i),
    ).toBeInTheDocument();
    expect(mockCreateQueue).not.toHaveBeenCalled();
  });
});
