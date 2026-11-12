import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent } from "src/utils/test-utils";
import { useQueueItemsForSource } from "src/api/annotation-queues/annotation-queues";
import { useScoresForSource, useSpanNotes } from "src/api/scores/scores";
import SpanDetailPane from "../SpanDetailPane";

// A shared View only link reads the span's annotations like the app does,
// but nothing on it writes or opens the annotation queue.
const mockState = vi.hoisted(() => ({
  scoresBySource: {},
  spanNotesBySource: {},
  queueEntries: [],
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/api/scores/scores", async (importOriginal) => ({
  ...(await importOriginal()),
  useScoresForSource: vi.fn((sourceType) => ({
    isLoading: false,
    data: mockState.scoresBySource[sourceType] || [],
  })),
  useSpanNotes: vi.fn((spanId) => ({
    data: mockState.spanNotesBySource[spanId] || [],
  })),
}));

vi.mock(
  "src/api/annotation-queues/annotation-queues",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useQueueItemsForSource: vi.fn(() => ({ data: mockState.queueEntries })),
  }),
);

const entry = {
  observation_span: {
    id: "span-1",
    trace: "trace-1",
    name: "llm call",
  },
};

const renderPane = (props) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SpanDetailPane
        entry={entry}
        projectId="project-1"
        onClose={() => {}}
        {...props}
      />
    </QueryClientProvider>,
  );

// What a restricted link's trace read carried for span-1.
const sharedFromLink = {
  scores: [
    {
      id: "score-shared",
      label_id: "label-1",
      source_type: "observation_span",
      source_id: "span-1",
      label_name: "walk label",
      label_type: "text",
      value: { text: "walk note" },
      annotator_name: "Owner",
      score_source: "human",
      notes: "",
      updated_at: "2026-11-10T10:00:00Z",
    },
  ],
  spanNotes: [
    { id: "note-shared", notes: "owner span note", annotator: "Reviewer" },
  ],
};

const clearAnnotationReads = () => {
  vi.mocked(useScoresForSource).mockClear();
  vi.mocked(useSpanNotes).mockClear();
  vi.mocked(useQueueItemsForSource).mockClear();
};

// With the link's annotations the viewer-session reads are switched off,
// so none of them sends a request.
const expectViewerReadsOff = () => {
  expect(vi.mocked(useScoresForSource)).toHaveBeenCalled();
  for (const call of vi.mocked(useScoresForSource).mock.calls) {
    expect(call[2]).toEqual(expect.objectContaining({ enabled: false }));
  }
  expect(vi.mocked(useSpanNotes)).toHaveBeenCalled();
  for (const call of vi.mocked(useSpanNotes).mock.calls) {
    expect(call[1]).toEqual(expect.objectContaining({ enabled: false }));
  }
  for (const call of vi.mocked(useQueueItemsForSource).mock.calls) {
    expect(call[1]).toEqual(expect.objectContaining({ enabled: false }));
  }
};

describe("SpanDetailPane annotations on a View only link", () => {
  const originalOpen = window.open;

  beforeEach(() => {
    window.open = vi.fn();
    vi.mocked(useQueueItemsForSource).mockClear();
    mockState.scoresBySource = {
      observation_span: [
        {
          id: "score-1",
          label_id: "label-1",
          source_type: "observation_span",
          source_id: "span-1",
          label_name: "category",
          label_type: "thumbs_up_down",
          value: { value: "up" },
          annotator_name: "Kartik",
          score_source: "human",
          notes: "",
          updated_at: "2026-05-09T19:13:00Z",
          queue_id: "queue-1",
          queue_item: "item-1",
        },
      ],
    };
    mockState.spanNotesBySource = {
      "span-1": [{ id: "note-1", notes: "looks right", annotator: "Kartik" }],
    };
    mockState.queueEntries = [];
  });

  afterEach(() => {
    window.open = originalOpen;
  });

  it("lists existing annotations read-only with the View only link reason", async () => {
    const user = userEvent.setup();
    renderPane({ readOnly: true, onAction: vi.fn() });

    await user.click(screen.getByText("Annotations"));

    expect(screen.getByText("category")).toBeInTheDocument();
    expect(screen.getByText("looks right")).toBeInTheDocument();
    expect(screen.getByText("View only link")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add Label" })).toBeNull();

    await user.click(screen.getByText("category"));
    expect(window.open).not.toHaveBeenCalled();

    // The queue-item lookup that only serves row clicks stays off.
    expect(vi.mocked(useQueueItemsForSource).mock.lastCall[1]).toEqual(
      expect.objectContaining({ enabled: false }),
    );
  });

  it("keeps Add Label and opening the queue item when it can act", async () => {
    const user = userEvent.setup();
    renderPane({ onAction: vi.fn() });

    await user.click(screen.getByText("Annotations"));

    expect(
      screen.getByRole("button", { name: "Add Label" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("View only link")).toBeNull();

    await user.click(screen.getByText("category"));
    expect(window.open).toHaveBeenCalledTimes(1);
    expect(window.open.mock.calls[0][0]).toContain("queue-1");
    expect(window.open.mock.calls[0][0]).toContain("itemId=item-1");
  });

  it("asks for no annotations and says so when the viewer is signed out", async () => {
    const user = userEvent.setup();
    vi.mocked(useScoresForSource).mockClear();
    vi.mocked(useSpanNotes).mockClear();
    vi.mocked(useQueueItemsForSource).mockClear();
    renderPane({ readOnly: true, annotationsUnavailable: true });

    await user.click(screen.getByText("Annotations"));

    expect(
      screen.getByText("This link does not include annotations."),
    ).toBeInTheDocument();
    expect(screen.queryByText("category")).toBeNull();
    expect(vi.mocked(useScoresForSource)).not.toHaveBeenCalled();
    expect(vi.mocked(useSpanNotes)).not.toHaveBeenCalled();
    expect(vi.mocked(useQueueItemsForSource)).not.toHaveBeenCalled();
  });

  it("lists the annotations a restricted link read, never the viewer's own", async () => {
    const user = userEvent.setup();
    clearAnnotationReads();
    // What the viewer's own session would answer: another organization's
    // scope, which must not stand in for the sharer's span.
    mockState.scoresBySource = {
      observation_span: [
        {
          id: "score-viewer",
          label_id: "label-2",
          source_type: "observation_span",
          source_id: "span-1",
          label_name: "viewer org label",
          label_type: "text",
          value: { text: "viewer org value" },
          annotator_name: "Viewer",
          score_source: "human",
          notes: "",
          updated_at: "2026-11-10T09:00:00Z",
        },
      ],
    };
    mockState.spanNotesBySource = {
      "span-1": [{ id: "note-viewer", notes: "viewer org note" }],
    };

    // A signed-in invited teammate on a restricted link.
    renderPane({
      readOnly: true,
      onAction: vi.fn(),
      sharedAnnotations: sharedFromLink,
    });

    await user.click(screen.getByText("Annotations"));

    expect(screen.getByText("walk label")).toBeInTheDocument();
    expect(screen.getByText("walk note")).toBeInTheDocument();
    expect(screen.getByText("Owner")).toBeInTheDocument();
    expect(screen.getByText("owner span note")).toBeInTheDocument();
    expect(screen.getByText("Reviewer")).toBeInTheDocument();
    expect(screen.getByText("View only link")).toBeInTheDocument();
    expect(screen.queryByText("No annotations yet.")).toBeNull();
    expect(
      screen.queryByText("This link does not include annotations."),
    ).toBeNull();
    expect(screen.queryByText("viewer org label")).toBeNull();
    expect(screen.queryByText("viewer org note")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add Label" })).toBeNull();
    expectViewerReadsOff();
  });

  it("shows an empty list when the link carried no annotations", async () => {
    const user = userEvent.setup();
    clearAnnotationReads();
    renderPane({
      readOnly: true,
      sharedAnnotations: { scores: [], spanNotes: [] },
    });

    await user.click(screen.getByText("Annotations"));

    expect(screen.getByText("No annotations yet.")).toBeInTheDocument();
    // The viewer's own "category" score does not leak onto the shared page.
    expect(screen.queryByText("category")).toBeNull();
    expect(screen.queryByText("looks right")).toBeNull();
    expectViewerReadsOff();
  });

  it("waits without asking while sign-in is still being checked", async () => {
    const user = userEvent.setup();
    clearAnnotationReads();
    // A public link while the auth context is still loading: not yet known
    // to be signed out, so not the signed-out line either.
    renderPane({
      readOnly: true,
      annotationsUnavailable: true,
      annotationsPending: true,
    });

    await user.click(screen.getByText("Annotations"));

    expect(
      screen.getByRole("progressbar", { name: "Loading" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("This link does not include annotations."),
    ).toBeNull();
    expect(screen.queryByText("No annotations yet.")).toBeNull();
    expect(screen.queryByText("category")).toBeNull();
    expect(vi.mocked(useScoresForSource)).not.toHaveBeenCalled();
    expect(vi.mocked(useSpanNotes)).not.toHaveBeenCalled();
    expect(vi.mocked(useQueueItemsForSource)).not.toHaveBeenCalled();
  });

  it("lists the link's annotations even while sign-in is still being checked", async () => {
    const user = userEvent.setup();
    clearAnnotationReads();
    // The rows are already on the page; listing them needs no session.
    renderPane({
      readOnly: true,
      annotationsUnavailable: true,
      annotationsPending: true,
      sharedAnnotations: sharedFromLink,
    });

    await user.click(screen.getByText("Annotations"));

    expect(screen.getByText("walk label")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar", { name: "Loading" })).toBeNull();
    expect(
      screen.queryByText("This link does not include annotations."),
    ).toBeNull();
    expectViewerReadsOff();
  });

  it("reads with the viewer's own session in the app, as before", async () => {
    const user = userEvent.setup();
    clearAnnotationReads();
    renderPane({ onAction: vi.fn() });

    await user.click(screen.getByText("Annotations"));

    expect(screen.getByText("category")).toBeInTheDocument();
    expect(vi.mocked(useScoresForSource).mock.lastCall[2]).not.toHaveProperty(
      "enabled",
    );
    expect(vi.mocked(useSpanNotes).mock.lastCall[1]).not.toHaveProperty(
      "enabled",
    );
    expect(vi.mocked(useQueueItemsForSource).mock.lastCall[1]).toEqual(
      expect.objectContaining({ enabled: true }),
    );
  });
});
