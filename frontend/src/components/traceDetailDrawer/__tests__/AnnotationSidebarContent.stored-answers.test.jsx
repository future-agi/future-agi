import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { enqueueSnackbar } from "notistack";
import AnnotationSidebarContent from "../AnnotationSidebarContent";

// The trace's Annotate sidebar (Observe trace > Actions > Annotate) and the
// queue's annotate screen read a stored answer the same way: it is drawn as
// stored, and one that breaks its label's current limits says why under it
// without blocking the other labels. A new or changed answer that breaks the
// limits is refused, in the queue's words.

const { mockBulkCreate, mockQueueItems } = vi.hoisted(() => ({
  mockBulkCreate: vi.fn(),
  mockQueueItems: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, sx, ...props }) => {
    void sx;
    return <span data-testid="iconify" data-icon={icon} {...props} />;
  },
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useQueueItemsForSource: mockQueueItems,
}));
vi.mock("src/api/scores/scores", () => ({
  useBulkCreateScores: () => ({ mutate: mockBulkCreate, isPending: false }),
}));

const sources = [
  { sourceType: "trace", sourceId: "trace-1", projectId: "project-1" },
];

const RATING = {
  id: "label-rating",
  name: "Rating",
  type: "star",
  settings: { no_of_stars: 5 },
};
const COMMENT = {
  id: "label-comment",
  name: "Comment",
  type: "text",
  settings: { placeholder: "Write comment", min_length: 50, max_length: 500 },
};
const QUALITY = {
  id: "label-quality",
  name: "Quality",
  type: "thumbs_up_down",
  settings: {},
};

function provideEntry({ labels, existingScores }) {
  mockQueueItems.mockReturnValue({
    data: [
      {
        queue: { id: "queue-1", name: "Trace review queue", instructions: "" },
        item: {
          id: "item-1",
          status: "completed",
          source_type: "trace",
          source_id: "trace-1",
        },
        labels,
        existing_scores: existingScores,
        existing_notes: "",
        existing_label_notes: {},
      },
    ],
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  });
}

const filledStars = () =>
  document.querySelectorAll('[data-icon="solar:star-bold"]').length;
const emptyStars = () =>
  document.querySelectorAll('[data-icon="solar:star-line-duotone"]').length;

const clickUpdate = () =>
  fireEvent.click(screen.getByRole("button", { name: /update/i }));

describe("the trace's Annotate sidebar with stored answers", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("draws a stored 5 on a 5-star label as 5 filled stars", () => {
    provideEntry({
      labels: [RATING],
      existingScores: { [RATING.id]: { rating: 5 } },
    });
    render(<AnnotationSidebarContent sources={sources} />);

    expect(filledStars()).toBe(5);
    expect(emptyStars()).toBe(0);
  });

  it("says quietly why a stored 5 does not fit a 3-star label", () => {
    const rating = { ...RATING, settings: { no_of_stars: 3 } };
    provideEntry({
      labels: [rating],
      existingScores: { [rating.id]: { rating: 5 } },
    });
    render(<AnnotationSidebarContent sources={sources} />);

    const reason = screen.getByText("Rating is 1 to 3 stars.");
    expect(reason).not.toHaveAttribute("role");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says quietly why a stored answer is too short and saves the other label", () => {
    provideEntry({
      labels: [COMMENT, QUALITY],
      existingScores: {
        [COMMENT.id]: { text: "Too short." },
        [QUALITY.id]: { value: "up" },
      },
    });
    render(<AnnotationSidebarContent sources={sources} />);

    const reason = screen.getByText(
      "Comment needs at least 50 characters (you wrote 10).",
    );
    expect(reason).not.toHaveAttribute("role");

    fireEvent.click(screen.getByText("No"));
    clickUpdate();

    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(mockBulkCreate).toHaveBeenCalledTimes(1);
    expect(mockBulkCreate.mock.calls[0][0].scores).toEqual([
      { label_id: COMMENT.id, value: { text: "Too short." }, notes: "" },
      { label_id: QUALITY.id, value: { value: "down" }, notes: "" },
    ]);
  });

  it("refuses a changed answer that is too short, in the queue's words", () => {
    provideEntry({
      labels: [COMMENT, QUALITY],
      existingScores: {
        [COMMENT.id]: { text: "Too short." },
        [QUALITY.id]: { value: "up" },
      },
    });
    render(<AnnotationSidebarContent sources={sources} />);

    fireEvent.change(screen.getByPlaceholderText("Write comment"), {
      target: { value: "Still short" },
    });
    clickUpdate();

    expect(mockBulkCreate).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Fix 1 score before submitting: Comment needs at least 50 characters (you wrote 11).",
      { variant: "warning" },
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Comment needs at least 50 characters (you wrote 11).",
    );
  });
});
