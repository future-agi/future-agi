import { beforeEach, describe, expect, it, vi } from "vitest";
import { enqueueSnackbar } from "notistack";
import userEvent from "@testing-library/user-event";
import { render, screen, waitFor } from "src/utils/test-utils";
import AddToQueueDialog from "../add-to-queue-dialog";
import {
  useAddQueueItems,
  useAnnotationQueuesList,
} from "src/api/annotation-queues/annotation-queues";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/sections/annotations/queues/create-queue-drawer", () => ({
  default: () => null,
}));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useAnnotationQueuesList: vi.fn(),
  useAddQueueItems: vi.fn(),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

describe("AddToQueueDialog", () => {
  const addItems = vi.fn();

  function renderDialog(props = {}) {
    const anchor = document.createElement("button");
    document.body.appendChild(anchor);

    return render(
      <AddToQueueDialog
        anchorEl={anchor}
        onClose={vi.fn()}
        sourceType="trace"
        sourceIds={["trace-1"]}
        {...props}
      />,
    );
  }

  beforeEach(() => {
    vi.clearAllMocks();
    useAddQueueItems.mockReturnValue({ mutate: addItems, isPending: false });
  });

  it("only lists active queues the backend marks as manager-visible for add-items", async () => {
    useAnnotationQueuesList.mockReturnValue({
      data: {
        results: [
          {
            id: "manager-queue",
            name: "Manager Queue",
            status: "active",
            viewer_role: "manager",
            viewer_roles: ["manager", "reviewer", "annotator"],
          },
          {
            id: "legacy-manager-queue",
            name: "Legacy Manager Queue",
            status: "active",
            viewer_role: "manager",
          },
          {
            id: "reviewer-queue",
            name: "Reviewer Queue",
            status: "active",
            viewer_role: "reviewer",
            viewer_roles: ["reviewer", "annotator"],
          },
          {
            id: "annotator-queue",
            name: "Annotator Queue",
            status: "active",
            viewer_role: "annotator",
            viewer_roles: ["annotator"],
          },
          {
            id: "completed-manager-queue",
            name: "Completed Manager Queue",
            status: "completed",
            viewer_role: "manager",
            viewer_roles: ["manager"],
          },
        ],
      },
      isLoading: false,
    });

    renderDialog();

    expect(await screen.findByText("Legacy Manager Queue")).toBeInTheDocument();
    expect(screen.getByText("Manager Queue")).toBeInTheDocument();
    expect(screen.queryByText("Reviewer Queue")).not.toBeInTheDocument();
    expect(screen.queryByText("Annotator Queue")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Completed Manager Queue"),
    ).not.toBeInTheDocument();
  });

  it("adds items only to a listed manager queue", async () => {
    const user = userEvent.setup();
    useAnnotationQueuesList.mockReturnValue({
      data: {
        results: [
          {
            id: "manager-queue",
            name: "Manager Queue",
            status: "active",
            viewer_role: "manager",
            viewer_roles: ["manager"],
          },
          {
            id: "annotator-queue",
            name: "Annotator Queue",
            status: "active",
            viewer_role: "annotator",
            viewer_roles: ["annotator"],
          },
        ],
      },
      isLoading: false,
    });

    renderDialog({ itemName: "Trace" });

    await user.click(await screen.findByText("Manager Queue"));

    await waitFor(() => {
      expect(addItems).toHaveBeenCalledWith(
        {
          queueId: "manager-queue",
          items: [{ source_type: "trace", source_id: "trace-1" }],
        },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      );
    });
    expect(screen.queryByText("Annotator Queue")).not.toBeInTheDocument();
  });

  it("surfaces partial backend skips when some selected traces are unavailable", async () => {
    const user = userEvent.setup();
    useAnnotationQueuesList.mockReturnValue({
      data: {
        results: [
          {
            id: "manager-queue",
            name: "Manager Queue",
            status: "active",
            viewer_role: "manager",
            viewer_roles: ["manager"],
          },
        ],
      },
      isLoading: false,
    });

    renderDialog({ itemName: "Trace", sourceIds: ["trace-1", "trace-2"] });

    await user.click(await screen.findByText("Manager Queue"));
    await waitFor(() => expect(addItems).toHaveBeenCalled());

    const onSuccess = addItems.mock.calls[0][1].onSuccess;
    onSuccess({
      data: {
        result: {
          added: 1,
          duplicates: 0,
          errors: [
            "1 trace is still in progress and was not added to the annotation queue.",
          ],
          queue_status: "active",
        },
      },
    });

    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "1 trace added to Manager Queue · 1 trace is still in progress and was not added to the annotation queue.",
      { variant: "info" },
    );
  });
});
