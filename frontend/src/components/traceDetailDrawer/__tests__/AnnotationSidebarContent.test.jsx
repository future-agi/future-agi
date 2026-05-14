import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import AnnotationSidebarContent from "../AnnotationSidebarContent";

const { mockBulkCreate, mockRefetch } = vi.hoisted(() => ({
  mockBulkCreate: vi.fn(),
  mockRefetch: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, sx, ...props }) => {
    void sx;
    return <span data-testid="iconify" data-icon={icon} {...props} />;
  },
}));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useQueueItemsForSource: vi.fn(() => ({
    data: [
      {
        queue: {
          id: "queue-1",
          name: "Trace review queue",
          instructions: "",
        },
        item: {
          id: "item-1",
          status: "completed",
          source_type: "trace",
          source_id: "trace-1",
        },
        labels: [
          {
            id: "label-thumbs",
            name: "thumbs",
            type: "thumbs_up_down",
            settings: {},
            allow_notes: true,
          },
          {
            id: "label-text",
            name: "summary",
            type: "text",
            settings: { placeholder: "Write summary" },
            allow_notes: true,
          },
        ],
        existingScores: {
          "label-thumbs": { value: "up" },
          "label-text": { text: "existing summary" },
        },
        existingNotes: "existing whole-item note",
        existingLabelNotes: {
          "label-thumbs": "thumbs note",
          "label-text": "summary note",
        },
        spanNotesSourceId: "span-1",
      },
    ],
    isLoading: false,
    isFetching: false,
    refetch: mockRefetch,
  })),
}));

vi.mock("src/api/scores/scores", () => ({
  useBulkCreateScores: vi.fn(() => ({
    mutate: mockBulkCreate,
    isPending: false,
  })),
}));

describe("AnnotationSidebarContent", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("saves trace drawer annotations with label notes and the separate span notes source", async () => {
    const user = userEvent.setup();

    render(
      <AnnotationSidebarContent
        sources={[
          {
            sourceType: "trace",
            sourceId: "trace-1",
            spanNotesSourceId: "span-1",
          },
        ]}
      />,
    );

    expect(screen.getByText("Trace review queue")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /update/i })).toBeEnabled();
    });

    await user.click(screen.getByRole("button", { name: /update/i }));

    expect(mockBulkCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        sourceType: "trace",
        sourceId: "trace-1",
        spanNotes: "existing whole-item note",
        spanNotesSourceId: "span-1",
        includeSpanNotes: true,
        scores: [
          {
            label_id: "label-thumbs",
            value: { value: "up" },
            notes: "thumbs note",
          },
          {
            label_id: "label-text",
            value: { text: "existing summary" },
            notes: "summary note",
          },
        ],
      }),
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });
});
