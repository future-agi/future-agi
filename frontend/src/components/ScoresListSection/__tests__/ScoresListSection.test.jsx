import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import ScoresListSection from "../ScoresListSection";

const mockState = vi.hoisted(() => ({
  scoresBySource: {},
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
  useSpanNotes: vi.fn(() => ({ data: [] })),
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
});
