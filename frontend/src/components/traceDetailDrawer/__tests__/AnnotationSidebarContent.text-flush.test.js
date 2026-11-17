import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import AnnotationSidebarContent from "../AnnotationSidebarContent";

const { mockBulkCreate, mockQueueItems } = vi.hoisted(() => ({
  mockBulkCreate: vi.fn(),
  mockQueueItems: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useQueueItemsForSource: mockQueueItems,
}));
vi.mock("src/api/scores/scores", () => ({
  useBulkCreateScores: () => ({
    mutate: mockBulkCreate,
    isPending: false,
  }),
}));

// Keep LabelInput real: its local text and 300 ms parent debounce are the seam.
const sources = [
  {
    sourceType: "trace",
    sourceId: "trace-1",
    spanNotesSourceId: "span-1",
    projectId: "project-1",
  },
];

function makeEntry(overrides = {}) {
  return {
    queue: { id: "queue-1", name: "Review queue" },
    item: { id: "item-1", status: "completed", source_type: "trace" },
    labels: [
      {
        id: "label-summary",
        name: "Summary",
        type: "text",
        settings: { placeholder: "Write summary" },
      },
      {
        id: "label-reason",
        name: "Reason",
        type: "text",
        settings: { placeholder: "Write reason" },
      },
      {
        id: "label-thumbs",
        name: "Quality",
        type: "thumbs_up_down",
        settings: {},
      },
    ],
    existing_scores: {
      "label-summary": { text: "old summary" },
      "label-reason": { text: "old reason" },
      "label-thumbs": { value: "up" },
    },
    existing_notes: "span note",
    existing_label_notes: { "label-summary": "summary note" },
    ...overrides,
  };
}

function provideEntries(entries) {
  mockQueueItems.mockReturnValue({
    data: entries,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  });
}

function renderSidebar() {
  return render(createElement(AnnotationSidebarContent, { sources }));
}

function editText(placeholder, text) {
  const input = screen.getByPlaceholderText(placeholder);
  fireEvent.change(input, { target: { value: text } });
  expect(input).toHaveValue(text);
  return input;
}

function submitWithShortcut(input, modifier = "ctrlKey") {
  fireEvent.keyDown(input, { key: "Enter", [modifier]: true });
}

describe("AnnotationSidebarContent text submission", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    provideEntries([makeEntry()]);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it.each([
    ["Save button", "pending", null],
    ["Update button", "completed", null],
    ["Ctrl+Enter", "completed", "ctrlKey"],
    ["Cmd+Enter", "completed", "metaKey"],
  ])(
    "%s saves both pending text edits before the debounce",
    (method, status, modifier) => {
      provideEntries([
        makeEntry({ item: { id: "item-1", status, source_type: "trace" } }),
      ]);
      renderSidebar();

      editText("Write summary", "latest summary");
      const reason = editText("Write reason", "latest reason");
      expect(mockBulkCreate).not.toHaveBeenCalled();

      // Do not advance timers or await a render between editing and submitting.
      if (modifier) {
        submitWithShortcut(reason, modifier);
      } else {
        const button = screen.getByRole("button", {
          name: method === "Save button" ? /save/i : /update/i,
        });
        expect(button).toBeEnabled();
        fireEvent.click(button);
      }

      expect(mockBulkCreate).toHaveBeenCalledTimes(1);
      expect(mockBulkCreate).toHaveBeenCalledWith(
        {
          sourceType: "trace",
          sourceId: "trace-1",
          queueItemId: "item-1",
          projectId: "project-1",
          scores: [
            {
              label_id: "label-summary",
              value: { text: "latest summary" },
              notes: "summary note",
            },
            {
              label_id: "label-reason",
              value: { text: "latest reason" },
              notes: "",
            },
            {
              label_id: "label-thumbs",
              value: { value: "up" },
              notes: "",
            },
          ],
          notes: "",
          spanNotes: "span note",
          spanNotesSourceId: "span-1",
          includeSpanNotes: true,
        },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      );

      act(() => vi.advanceTimersByTime(300));
      expect(mockBulkCreate).toHaveBeenCalledTimes(1);
      expect(screen.getByPlaceholderText("Write summary")).toHaveValue(
        "latest summary",
      );
      expect(reason).toHaveValue("latest reason");
    },
  );

  it("submits the first pending text score with Ctrl+Enter", () => {
    provideEntries([
      makeEntry({ existing_scores: {}, existing_label_notes: {} }),
    ]);
    renderSidebar();

    const input = editText("Write summary", "first summary");
    submitWithShortcut(input);

    expect(mockBulkCreate).toHaveBeenCalledTimes(1);
    expect(mockBulkCreate.mock.calls[0][0].scores).toEqual([
      {
        label_id: "label-summary",
        value: { text: "first summary" },
        notes: "",
      },
    ]);
  });

  it("submits a pending text clear instead of restoring the old score", () => {
    renderSidebar();
    editText("Write summary", "");
    fireEvent.click(screen.getByRole("button", { name: /update/i }));

    expect(mockBulkCreate).toHaveBeenCalledTimes(1);
    expect(mockBulkCreate.mock.calls[0][0].scores).toContainEqual({
      label_id: "label-summary",
      value: { text: "" },
      notes: "summary note",
    });
  });

  it("keeps hydrated scores when there are no pending text edits", () => {
    renderSidebar();
    fireEvent.click(screen.getByRole("button", { name: /update/i }));

    expect(mockBulkCreate).toHaveBeenCalledTimes(1);
    expect(mockBulkCreate.mock.calls[0][0].scores).toEqual([
      {
        label_id: "label-summary",
        value: { text: "old summary" },
        notes: "summary note",
      },
      { label_id: "label-reason", value: { text: "old reason" }, notes: "" },
      { label_id: "label-thumbs", value: { value: "up" }, notes: "" },
    ]);
  });

  it("keeps edits delivered by the normal debounce", () => {
    renderSidebar();
    editText("Write summary", "settled summary");
    act(() => vi.advanceTimersByTime(300));
    fireEvent.click(screen.getByRole("button", { name: /update/i }));

    expect(mockBulkCreate).toHaveBeenCalledTimes(1);
    expect(mockBulkCreate.mock.calls[0][0].scores).toContainEqual({
      label_id: "label-summary",
      value: { text: "settled summary" },
      notes: "summary note",
    });
  });

  it("does not reuse scores after switching to an unscored source", () => {
    const view = renderSidebar();
    provideEntries([
      makeEntry({
        item: { id: "item-2", status: "pending", source_type: "trace" },
        existing_scores: {},
        existing_label_notes: {},
      }),
    ]);
    view.rerender(
      createElement(AnnotationSidebarContent, {
        sources: [
          { sourceType: "trace", sourceId: "trace-2", projectId: "project-1" },
        ],
      }),
    );

    const input = screen.getByPlaceholderText("Write summary");
    expect(input).toHaveValue("");
    submitWithShortcut(input);
    expect(mockBulkCreate).not.toHaveBeenCalled();

    editText("Write summary", "new source summary");
    submitWithShortcut(input);
    expect(mockBulkCreate).toHaveBeenCalledTimes(1);
    expect(mockBulkCreate.mock.calls[0][0]).toEqual(
      expect.objectContaining({
        sourceId: "trace-2",
        queueItemId: "item-2",
        scores: [
          {
            label_id: "label-summary",
            value: { text: "new source summary" },
            notes: "",
          },
        ],
      }),
    );
  });

  it("flushes only the submitted queue section", () => {
    const second = makeEntry({
      queue: { id: "queue-2", name: "Second queue" },
      item: { id: "item-2", status: "completed", source_type: "trace" },
    });
    second.labels = second.labels.map((label) =>
      label.type === "text"
        ? { ...label, settings: { placeholder: `Second ${label.name}` } }
        : label,
    );
    provideEntries([makeEntry(), second]);
    renderSidebar();

    const firstInput = editText("Write summary", "first queue draft");
    const secondInput = editText("Second Summary", "second queue draft");
    submitWithShortcut(firstInput);

    expect(mockBulkCreate).toHaveBeenCalledTimes(1);
    expect(mockBulkCreate.mock.calls[0][0]).toEqual(
      expect.objectContaining({
        queueItemId: "item-1",
        scores: expect.arrayContaining([
          {
            label_id: "label-summary",
            value: { text: "first queue draft" },
            notes: "summary note",
          },
        ]),
      }),
    );
    expect(secondInput).toHaveValue("second queue draft");

    submitWithShortcut(secondInput);
    expect(mockBulkCreate).toHaveBeenCalledTimes(2);
    expect(mockBulkCreate.mock.calls[1][0]).toEqual(
      expect.objectContaining({
        queueItemId: "item-2",
        scores: expect.arrayContaining([
          {
            label_id: "label-summary",
            value: { text: "second queue draft" },
            notes: "summary note",
          },
        ]),
      }),
    );
  });
});
