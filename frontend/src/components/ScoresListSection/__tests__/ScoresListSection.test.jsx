import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import ScoresListSection from "../ScoresListSection";

const mockState = vi.hoisted(() => ({
  scoresBySource: {},
  spanNotesBySource: {},
  spanNoteSourceIds: [],
  queueEntries: [],
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/utils/format-time", () => ({
  fDateTime: () => "09 May 2026 7:13 PM",
}));

vi.mock("src/api/scores/scores", () => ({
  useScoresForSource: vi.fn((sourceType) => ({
    isLoading: false,
    data: mockState.scoresBySource[sourceType] || [],
  })),
  useSpanNotes: vi.fn((spanId) => {
    mockState.spanNoteSourceIds.push(spanId);
    return { data: mockState.spanNotesBySource[spanId] || [] };
  }),
}));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useQueueItemsForSource: vi.fn(() => ({
    data: mockState.queueEntries,
  })),
}));

describe("ScoresListSection", () => {
  const originalOpen = window.open;

  beforeEach(() => {
    window.open = vi.fn();
    mockState.scoresBySource = {
      trace: [
        {
          id: "score-1",
          labelId: "label-1",
          sourceType: "trace",
          sourceId: "trace-1",
          labelName: "category",
          labelType: "thumbs_up_down",
          value: { value: "up" },
          annotatorName: "Kartik",
          scoreSource: "human",
          notes: "",
          updated_at: "2026-05-09T19:13:00Z",
          queueId: "queue-1",
          queueItem: "item-1",
        },
      ],
    };
    mockState.spanNotesBySource = {};
    mockState.spanNoteSourceIds = [];
    mockState.queueEntries = [];
  });

  afterEach(() => {
    window.open = originalOpen;
  });

  it("does not open rows unless queue row linking is enabled", async () => {
    const user = userEvent.setup();

    render(<ScoresListSection sourceType="trace" sourceId="trace-1" />);

    await user.click(screen.getByText("category"));

    expect(window.open).not.toHaveBeenCalled();
  });

  it("opens the owning annotation queue item in a new tab when enabled", async () => {
    const user = userEvent.setup();

    render(
      <ScoresListSection
        sourceType="trace"
        sourceId="trace-1"
        openQueueItemOnRowClick
      />,
    );

    await user.click(screen.getByText("category"));

    expect(window.open).toHaveBeenCalledWith(
      "/dashboard/annotations/queues/queue-1/annotate?itemId=item-1",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("maps live API snake_case score fields before opening a queue item", async () => {
    const user = userEvent.setup();
    mockState.scoresBySource = {
      trace: [
        {
          id: "score-1",
          label_id: "label-1",
          source_type: "trace",
          source_id: "trace-1",
          label_name: "live category",
          label_type: "text",
          value: { text: "review this answer" },
          annotator_name: "Kartik",
          score_source: "human",
          notes: "label note",
          updated_at: "2026-05-09T19:13:00Z",
          queue_id: "queue-1",
          queue_item: { id: "item-1" },
        },
      ],
    };

    render(
      <ScoresListSection
        sourceType="trace"
        sourceId="trace-1"
        openQueueItemOnRowClick
      />,
    );

    expect(screen.getByText("live category")).toBeInTheDocument();
    expect(screen.getByText("review this answer")).toBeInTheDocument();

    await user.click(screen.getByText("live category"));

    expect(window.open).toHaveBeenCalledWith(
      "/dashboard/annotations/queues/queue-1/annotate?itemId=item-1",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("keeps rows inert when queue row linking is enabled but no target exists", async () => {
    const user = userEvent.setup();
    mockState.scoresBySource = {
      trace: [
        {
          id: "score-1",
          labelId: "label-1",
          sourceType: "trace",
          sourceId: "trace-1",
          labelName: "unlinked category",
          labelType: "text",
          value: { text: "not in any queue" },
          annotatorName: "Kartik",
          scoreSource: "human",
          updated_at: "2026-05-09T19:13:00Z",
        },
      ],
    };
    mockState.queueEntries = [];

    render(
      <ScoresListSection
        sourceType="trace"
        sourceId="trace-1"
        openQueueItemOnRowClick
      />,
    );

    await user.click(screen.getByText("unlinked category"));

    expect(window.open).not.toHaveBeenCalled();
  });

  it("falls back to source queue items when the score has no queue item", async () => {
    const user = userEvent.setup();
    mockState.scoresBySource = {
      trace: [
        {
          id: "score-1",
          labelId: "label-1",
          sourceType: "trace",
          sourceId: "trace-1",
          labelName: "category",
          labelType: "thumbs_up_down",
          value: { value: "up" },
          annotatorName: "Kartik",
          scoreSource: "human",
          notes: "",
          updated_at: "2026-05-09T19:13:00Z",
        },
      ],
    };
    mockState.queueEntries = [
      {
        queue: { id: "queue-from-source" },
        item: {
          id: "item-from-source",
          sourceType: "trace",
          sourceId: "trace-1",
        },
        labels: [{ id: "label-1", name: "category" }],
      },
    ];

    render(
      <ScoresListSection
        sourceType="trace"
        sourceId="trace-1"
        openQueueItemOnRowClick
      />,
    );

    await user.click(screen.getByText("category"));

    expect(window.open).toHaveBeenCalledWith(
      "/dashboard/annotations/queues/queue-from-source/annotate?itemId=item-from-source",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("uses source-specific fallback when primary and secondary scores share a label", async () => {
    const user = userEvent.setup();
    mockState.scoresBySource = {
      trace: [
        {
          id: "trace-score",
          labelId: "label-1",
          sourceType: "trace",
          sourceId: "trace-1",
          labelName: "trace shared",
          labelType: "text",
          value: { text: "trace value" },
          annotatorName: "Kartik",
          scoreSource: "human",
          updated_at: "2026-05-09T19:13:00Z",
        },
      ],
      observation_span: [
        {
          id: "span-score",
          labelId: "label-1",
          sourceType: "observation_span",
          sourceId: "span-1",
          labelName: "span shared",
          labelType: "text",
          value: { text: "span value" },
          annotatorName: "Kartik",
          scoreSource: "human",
          updated_at: "2026-05-09T19:13:00Z",
        },
      ],
    };
    mockState.queueEntries = [
      {
        queue: { id: "trace-queue" },
        item: {
          id: "trace-item",
          source_type: "trace",
          source_id: "trace-1",
        },
        labels: [{ id: "label-1", name: "shared" }],
      },
      {
        queue: { id: "span-queue" },
        item: {
          id: "span-item",
          source_type: "observation_span",
          source_id: "span-1",
        },
        labels: [{ id: "label-1", name: "shared" }],
      },
    ];

    render(
      <ScoresListSection
        sourceType="trace"
        sourceId="trace-1"
        secondarySourceType="observation_span"
        secondarySourceId="span-1"
        openQueueItemOnRowClick
      />,
    );

    await user.click(screen.getByText("span shared"));

    expect(window.open).toHaveBeenCalledWith(
      "/dashboard/annotations/queues/span-queue/annotate?itemId=span-item",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("shows whole-item notes from the secondary observation span for trace scores", () => {
    mockState.spanNotesBySource = {
      "span-1": [
        {
          id: "note-1",
          notes: "whole item note",
          annotator: "Kartik",
        },
      ],
    };

    render(
      <ScoresListSection
        sourceType="trace"
        sourceId="trace-1"
        secondarySourceType="observation_span"
        secondarySourceId="span-1"
      />,
    );

    expect(mockState.spanNoteSourceIds).toContain("span-1");
    expect(screen.getByText("Span Notes")).toBeInTheDocument();
    expect(screen.getByText("whole item note")).toBeInTheDocument();
  });
});
