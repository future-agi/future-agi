import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { enqueueSnackbar } from "notistack";
import axios from "src/utils/axios";
import AnnotationSidebarContent from "../AnnotationSidebarContent";

// Saving from the trace's Annotate sidebar: when the server refuses some
// labels (POST /model-hub/scores/bulk/ answers failed_labels and
// failed_reasons, as the trace's add_annotations does), the sidebar says
// "Saved N of M labels. Not saved: …" and shows each reason under its
// label, never "Annotations saved". The real score hook reads the reply;
// only the HTTP transport is replaced.

const { mockQueueItems } = vi.hoisted(() => ({ mockQueueItems: vi.fn() }));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useQueueItemsForSource: mockQueueItems,
}));

const TOO_SHORT = "Text too short. Minimum length is 50 characters.";

// The sidebar was opened before the label gained its 50-character minimum.
const COMMENT = {
  id: "label-comment",
  name: "Comment",
  type: "text",
  settings: { placeholder: "Write comment" },
};
const QUALITY = {
  id: "label-quality",
  name: "Quality",
  type: "thumbs_up_down",
  settings: {},
};

function provideEntry() {
  mockQueueItems.mockReturnValue({
    data: [
      {
        queue: { id: "queue-1", name: "Trace review queue", instructions: "" },
        item: {
          id: "item-1",
          status: "pending",
          source_type: "trace",
          source_id: "trace-1",
        },
        labels: [COMMENT, QUALITY],
        existing_scores: {},
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

function reply(result) {
  axios.post.mockResolvedValueOnce({ data: { status: true, result } });
}

function renderSidebar() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AnnotationSidebarContent
        sources={[
          { sourceType: "trace", sourceId: "trace-1", projectId: "project-1" },
        ]}
      />
    </QueryClientProvider>,
  );
}

function answerAndSave() {
  fireEvent.change(screen.getByPlaceholderText("Write comment"), {
    target: { value: "Too short." },
  });
  fireEvent.click(screen.getByText("Yes"));
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
}

describe("saving from the trace's Annotate sidebar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    provideEntry();
  });

  it("says which label was not saved and why, never 'Annotations saved'", async () => {
    reply({
      scores: [{ id: "score-1", label_id: QUALITY.id, value: { value: "up" } }],
      errors: [TOO_SHORT],
      failed_labels: [COMMENT.id],
      failed_reasons: { [COMMENT.id]: TOO_SHORT },
      success_labels: [QUALITY.id],
    });
    renderSidebar();

    answerAndSave();

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Saved 1 of 2 labels. Not saved: Comment.",
        { variant: "warning" },
      ),
    );
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      "Annotations saved",
      expect.anything(),
    );
    expect(await screen.findByText(TOO_SHORT)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Write comment")).toHaveValue(
      "Too short.",
    );
    const sent = axios.post.mock.calls[0][1].scores;
    expect(sent).toHaveLength(2);
    expect(sent).toEqual(
      expect.arrayContaining([
        {
          label_id: COMMENT.id,
          value: { text: "Too short." },
          notes: "",
          score_source: "human",
        },
        {
          label_id: QUALITY.id,
          value: { value: "up" },
          notes: "",
          score_source: "human",
        },
      ]),
    );
  });

  it("says 'Annotations saved' when every label saved", async () => {
    reply({
      scores: [
        { id: "score-1", label_id: COMMENT.id, value: { text: "Too short." } },
        { id: "score-2", label_id: QUALITY.id, value: { value: "up" } },
      ],
      errors: [],
      failed_labels: [],
      failed_reasons: {},
      success_labels: [COMMENT.id, QUALITY.id],
    });
    renderSidebar();

    answerAndSave();

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith("Annotations saved", {
        variant: "success",
      }),
    );
    expect(screen.queryByText(TOO_SHORT)).not.toBeInTheDocument();
  });
});
