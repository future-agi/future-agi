/**
 * Phase 2B-3C – Annotation workspace component tests.
 * Tests: LabelInput, AnnotateHeader, AnnotateFooter, AnnotationHistory
 */
/* eslint-disable react/prop-types */
import { beforeEach, describe, it, expect, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import LabelInput from "../annotate/label-input";
import LabelPanel from "../annotate/label-panel";
import AnnotationComparisonPanel from "../annotate/annotation-comparison-panel";
import DiscussionPanel, {
  CollaborationDrawer,
} from "../annotate/discussion-panel";
import { ALL_ANNOTATORS } from "../annotate/annotation-view-mode";
import AnnotateHeader from "../annotate/annotate-header";
import AnnotateFooter from "../annotate/annotate-footer";
import AnnotationHistory from "../annotate/annotation-history";

const { mockCreateDiscussionComment } = vi.hoisted(() => ({
  mockCreateDiscussionComment: vi.fn(),
}));

const {
  mockResolveDiscussionThread,
  mockReopenDiscussionThread,
  mockToggleDiscussionReaction,
  mockDiscussionMutationState,
} = vi.hoisted(() => ({
  mockResolveDiscussionThread: vi.fn(),
  mockReopenDiscussionThread: vi.fn(),
  mockToggleDiscussionReaction: vi.fn(),
  mockDiscussionMutationState: {
    create: { isPending: false, variables: undefined },
    resolve: { isPending: false, variables: undefined },
    reopen: { isPending: false, variables: undefined },
    reaction: { isPending: false, variables: undefined },
  },
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, sx, ...props }) => {
    void sx;
    return <span data-testid="iconify" data-icon={icon} {...props} />;
  },
}));

vi.mock("src/utils/format-time", () => ({
  fDateTime: () => "Jan 1, 2025 12:00",
  fToNowStrict: () => "5 minutes ago",
}));

vi.mock("src/sections/common/CellMarkdown", () => ({
  default: ({ text }) => <div>{text}</div>,
}));

// Mock API hook for annotation history
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useItemAnnotations: vi.fn(() => ({ data: [] })),
  useCreateDiscussionComment: vi.fn(() => ({
    mutate: mockCreateDiscussionComment,
    ...mockDiscussionMutationState.create,
  })),
  useResolveDiscussionThread: vi.fn(() => ({
    mutate: mockResolveDiscussionThread,
    ...mockDiscussionMutationState.resolve,
  })),
  useReopenDiscussionThread: vi.fn(() => ({
    mutate: mockReopenDiscussionThread,
    ...mockDiscussionMutationState.reopen,
  })),
  useToggleDiscussionReaction: vi.fn(() => ({
    mutate: mockToggleDiscussionReaction,
    ...mockDiscussionMutationState.reaction,
  })),
}));

beforeEach(() => {
  mockCreateDiscussionComment.mockClear();
  mockResolveDiscussionThread.mockClear();
  mockReopenDiscussionThread.mockClear();
  mockToggleDiscussionReaction.mockClear();
  mockDiscussionMutationState.create = {
    isPending: false,
    variables: undefined,
  };
  mockDiscussionMutationState.resolve = {
    isPending: false,
    variables: undefined,
  };
  mockDiscussionMutationState.reopen = {
    isPending: false,
    variables: undefined,
  };
  mockDiscussionMutationState.reaction = {
    isPending: false,
    variables: undefined,
  };
});

// ---------------------------------------------------------------------------
// LabelInput
// ---------------------------------------------------------------------------
describe("LabelInput", () => {
  it("renders label name", () => {
    render(
      <LabelInput
        label={{ name: "Quality", type: "star", settings: { no_of_stars: 5 } }}
        value={{}}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("Quality")).toBeInTheDocument();
  });

  it("shows required indicator", () => {
    render(
      <LabelInput
        label={{ name: "Test", type: "text", settings: {}, required: true }}
        value={{}}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("*")).toBeInTheDocument();
  });

  it("shows description when provided", () => {
    render(
      <LabelInput
        label={{
          name: "Test",
          type: "text",
          settings: {},
          description: "Help text",
        }}
        value={{}}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("Help text")).toBeInTheDocument();
  });

  describe("star type", () => {
    it("renders star icons for each star", () => {
      render(
        <LabelInput
          label={{ name: "Stars", type: "star", settings: { no_of_stars: 5 } }}
          value={{ rating: 3 }}
          onChange={() => {}}
        />,
      );
      // Custom StarInput renders Iconify star icons
      const starIcons = screen
        .getAllByTestId("iconify")
        .filter(
          (el) =>
            el.getAttribute("data-icon") === "solar:star-bold" ||
            el.getAttribute("data-icon") === "solar:star-line-duotone",
        );
      expect(starIcons).toHaveLength(5);
    });
  });

  describe("categorical type (single)", () => {
    it("renders radio options", () => {
      render(
        <LabelInput
          label={{
            name: "Cat",
            type: "categorical",
            settings: { options: ["Good", "Bad"], multi_choice: false },
          }}
          value={{ selected: [] }}
          onChange={() => {}}
        />,
      );
      expect(screen.getByText("Good")).toBeInTheDocument();
      expect(screen.getByText("Bad")).toBeInTheDocument();
    });
  });

  describe("numeric type", () => {
    it("renders slider and text input", () => {
      render(
        <LabelInput
          label={{
            name: "Score",
            type: "numeric",
            settings: { min: 0, max: 10, step: 1 },
          }}
          value={{ value: 5 }}
          onChange={() => {}}
        />,
      );
      // MUI Slider has role slider
      expect(screen.getByRole("slider")).toBeInTheDocument();
      expect(screen.getByRole("spinbutton")).toBeInTheDocument();
    });
  });

  describe("text type", () => {
    it("renders textarea", () => {
      render(
        <LabelInput
          label={{
            name: "Comment",
            type: "text",
            settings: { placeholder: "Write here...", max_length: 500 },
          }}
          value={{ text: "" }}
          onChange={() => {}}
        />,
      );
      expect(screen.getByPlaceholderText("Write here...")).toBeInTheDocument();
    });

    it("calls onChange on text input (debounced)", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      render(
        <LabelInput
          label={{
            name: "Comment",
            type: "text",
            settings: { placeholder: "Enter text..." },
          }}
          value={{ text: "" }}
          onChange={onChange}
        />,
      );
      await user.type(screen.getByPlaceholderText("Enter text..."), "A");
      // DebouncedTextInput fires onChange after 300ms debounce
      await waitFor(
        () => {
          expect(onChange).toHaveBeenCalledWith({ text: "A" });
        },
        { timeout: 1000 },
      );
    });
  });

  describe("thumbs_up_down type", () => {
    it("renders Yes and No labels", () => {
      render(
        <LabelInput
          label={{ name: "Vote", type: "thumbs_up_down", settings: {} }}
          value={{}}
          onChange={() => {}}
        />,
      );
      expect(screen.getByText("Yes")).toBeInTheDocument();
      expect(screen.getByText("No")).toBeInTheDocument();
    });

    it("calls onChange with 'up' when Yes clicked", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      render(
        <LabelInput
          label={{ name: "Vote", type: "thumbs_up_down", settings: {} }}
          value={{}}
          onChange={onChange}
        />,
      );
      await user.click(screen.getByText("Yes"));
      expect(onChange).toHaveBeenCalledWith({ value: "up" });
    });
  });
});

// ---------------------------------------------------------------------------
// LabelPanel
// ---------------------------------------------------------------------------
describe("LabelPanel", () => {
  it("submits separate notes for each note-enabled label", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <LabelPanel
        labels={[
          {
            id: "ql-1",
            label_id: "label-1",
            name: "Content",
            type: "thumbs_up_down",
            settings: {},
            allow_notes: true,
          },
          {
            id: "ql-2",
            label_id: "label-2",
            name: "Latency",
            type: "thumbs_up_down",
            settings: {},
            allow_notes: true,
          },
        ]}
        annotations={[]}
        onSubmit={onSubmit}
        queueId="queue-1"
        itemId="item-1"
      />,
    );

    await user.click(screen.getAllByText("Yes")[0]);
    await user.click(screen.getAllByText("No")[1]);
    const noteFields = screen.getAllByPlaceholderText(
      "Add notes for this label...",
    );
    await user.type(noteFields[0], "content note");
    await user.type(noteFields[1], "latency note");
    await user.click(screen.getByRole("button", { name: /submit & next/i }));

    expect(onSubmit).toHaveBeenCalledWith({
      annotations: [
        {
          label_id: "label-1",
          value: { value: "up" },
          notes: "content note",
        },
        {
          label_id: "label-2",
          value: { value: "down" },
          notes: "latency note",
        },
      ],
      itemNotes: "",
    });
  });

  it("prefills and submits whole-item notes", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <LabelPanel
        labels={[
          {
            id: "ql-1",
            label_id: "label-1",
            name: "Content",
            type: "thumbs_up_down",
            settings: {},
            allow_notes: false,
          },
        ]}
        annotations={[]}
        initialItemNotes="existing whole item note"
        onSubmit={onSubmit}
        queueId="queue-1"
        itemId="item-1"
      />,
    );

    const itemNotes = screen.getByPlaceholderText("Add notes for this item...");
    expect(itemNotes).toHaveValue("existing whole item note");

    await user.clear(itemNotes);
    await user.type(itemNotes, "updated whole item note");
    await user.click(screen.getByText("Yes"));
    await user.click(screen.getByRole("button", { name: /submit & next/i }));

    expect(onSubmit).toHaveBeenCalledWith({
      annotations: [
        {
          label_id: "label-1",
          value: { value: "up" },
        },
      ],
      itemNotes: "updated whole item note",
    });
  });

  it("can label the submit action as submit for review", async () => {
    render(
      <LabelPanel
        labels={[
          {
            id: "ql-1",
            label_id: "label-1",
            name: "Content",
            type: "thumbs_up_down",
            settings: {},
            allow_notes: false,
          },
        ]}
        annotations={[
          {
            label_id: "label-1",
            value: { value: "up" },
          },
        ]}
        onSubmit={vi.fn()}
        queueId="queue-1"
        itemId="item-1"
        submitLabel="Submit for Review"
      />,
    );

    expect(
      screen.getByRole("button", { name: /submit for review/i }),
    ).toBeInTheDocument();
  });

  it("shows read-only reason and hides submit action", () => {
    render(
      <LabelPanel
        labels={[
          {
            id: "ql-1",
            label_id: "label-1",
            name: "Content",
            type: "thumbs_up_down",
            settings: {},
            allow_notes: false,
          },
        ]}
        annotations={[
          {
            label_id: "label-1",
            value: { value: "up" },
          },
        ]}
        onSubmit={vi.fn()}
        queueId="queue-1"
        itemId="item-1"
        readOnly
        readOnlyReason="This item is waiting for review."
      />,
    );

    expect(
      screen.getByText("This item is waiting for review."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /submit/i }),
    ).not.toBeInTheDocument();
  });

  it("does not use whole-item notes as label notes", async () => {
    render(
      <LabelPanel
        labels={[
          {
            id: "ql-1",
            label_id: "label-1",
            name: "Content",
            type: "thumbs_up_down",
            settings: {},
            allow_notes: true,
          },
        ]}
        annotations={[
          {
            label_id: "label-1",
            value: { value: "up" },
          },
        ]}
        initialItemNotes="trace-level-only note"
        onSubmit={vi.fn()}
        queueId="queue-1"
        itemId="item-1"
      />,
    );

    await waitFor(() => {
      expect(
        screen.getByPlaceholderText("Add notes for this item..."),
      ).toHaveValue("trace-level-only note");
      expect(
        screen.getByPlaceholderText("Add notes for this label..."),
      ).toHaveValue("");
    });
  });

  it("shows reviewer feedback on returned items", () => {
    render(
      <LabelPanel
        labels={[]}
        annotations={[]}
        reviewFeedback="Please re-check the sentiment label."
        onSubmit={vi.fn()}
        queueId="queue-1"
        itemId="item-1"
      />,
    );

    expect(screen.getByText("Reviewer feedback")).toBeInTheDocument();
    expect(
      screen.getByText("Please re-check the sentiment label."),
    ).toBeInTheDocument();
  });

  it("shows label-specific reviewer feedback on returned items", () => {
    render(
      <LabelPanel
        labels={[
          {
            id: "ql-1",
            label_id: "label-1",
            name: "Sentiment",
            type: "categorical",
            settings: {},
          },
        ]}
        annotations={[]}
        reviewComments={[
          {
            id: "comment-1",
            comment: "Please re-check the whole item.",
            reviewer_name: "Reviewer One",
          },
          {
            id: "comment-2",
            label_id: "label-1",
            label_name: "Sentiment",
            comment: "This label should be negative.",
            reviewer_name: "Reviewer One",
          },
        ]}
        onSubmit={vi.fn()}
        queueId="queue-1"
        itemId="item-1"
      />,
    );

    expect(
      screen.getByText("Please re-check the whole item."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("This label should be negative."),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Reviewer One").length).toBeGreaterThan(0);
  });

  it("shows feedback time and highlights the focused comment scope", () => {
    const { container } = render(
      <LabelPanel
        labels={[
          {
            id: "ql-1",
            label_id: "label-1",
            name: "Sentiment",
            type: "text",
            settings: {},
          },
        ]}
        annotations={[]}
        reviewComments={[
          {
            id: "comment-1",
            label_id: "label-1",
            label_name: "Sentiment",
            comment: "This label needs a second pass.",
            reviewer_name: "Reviewer One",
            created_at: "2025-01-01T12:00:00Z",
          },
        ]}
        focusedCommentScope="label:label-1"
        onSubmit={vi.fn()}
        queueId="queue-1"
        itemId="item-1"
      />,
    );

    expect(screen.getByText("5 minutes ago")).toBeInTheDocument();
    expect(
      container.querySelector('[data-review-label-id="label-1"]'),
    ).toHaveAttribute("data-comment-focus", "true");
  });

  it("only shows targeted reviewer feedback for the current annotator", () => {
    render(
      <LabelPanel
        labels={[
          {
            id: "ql-1",
            label_id: "label-1",
            name: "Sentiment",
            type: "categorical",
            settings: {},
          },
        ]}
        annotations={[]}
        reviewComments={[
          {
            id: "comment-1",
            label_id: "label-1",
            target_annotator_id: "user-1",
            comment: "Fix your sentiment value.",
            reviewer_name: "Reviewer One",
          },
          {
            id: "comment-2",
            label_id: "label-1",
            target_annotator_id: "user-2",
            comment: "Feedback for someone else.",
            reviewer_name: "Reviewer One",
          },
        ]}
        currentUserId="user-1"
        onSubmit={vi.fn()}
        queueId="queue-1"
        itemId="item-1"
      />,
    );

    expect(screen.getByText("Fix your sentiment value.")).toBeInTheDocument();
    expect(
      screen.queryByText("Feedback for someone else."),
    ).not.toBeInTheDocument();
  });

  it("hides targeted reviewer feedback until the current annotator is known", () => {
    render(
      <LabelPanel
        labels={[]}
        annotations={[]}
        reviewComments={[
          {
            id: "comment-1",
            action: "request_changes",
            blocking: true,
            thread_status: "open",
            target_annotator_id: "user-1",
            comment: "Private targeted feedback.",
            reviewer_name: "Reviewer One",
          },
        ]}
        onSubmit={vi.fn()}
        queueId="queue-1"
        itemId="item-1"
      />,
    );

    expect(
      screen.queryByText("Private targeted feedback."),
    ).not.toBeInTheDocument();
  });

  it("clears stale label notes when the selected annotator changes", async () => {
    const label = {
      id: "ql-1",
      label_id: "label-1",
      name: "Content",
      type: "thumbs_up_down",
      settings: {},
      allow_notes: true,
    };
    const annotations = [
      {
        label_id: "label-1",
        value: { value: "up" },
        notes: "previous annotator note",
      },
    ];

    const { rerender } = render(
      <LabelPanel
        labels={[label]}
        annotations={annotations}
        onSubmit={() => {}}
        queueId="queue-1"
        itemId="item-1"
        viewingAnnotatorId="user-1"
      />,
    );

    await waitFor(() => {
      expect(
        screen.getByPlaceholderText("Add notes for this label..."),
      ).toHaveValue("previous annotator note");
    });

    rerender(
      <LabelPanel
        labels={[label]}
        annotations={annotations}
        onSubmit={() => {}}
        queueId="queue-1"
        itemId="item-1"
        viewingAnnotatorId="user-2"
      />,
    );

    await waitFor(() => {
      expect(
        screen.getByPlaceholderText("Add notes for this label..."),
      ).toHaveValue("");
    });
  });

  it("shows selected annotator context and emits annotator changes", async () => {
    const user = userEvent.setup();
    const onViewingAnnotatorChange = vi.fn();

    render(
      <LabelPanel
        labels={[]}
        annotations={[]}
        onSubmit={() => {}}
        queueId="queue-1"
        itemId="item-1"
        annotators={[
          { user_id: "user-1", name: "Kartik" },
          { user_id: "user-2", name: "Narda" },
        ]}
        currentUserId="user-1"
        viewingAnnotatorId="user-2"
        onViewingAnnotatorChange={onViewingAnnotatorChange}
      />,
    );

    expect(
      screen.getByText("You are viewing annotations of Narda"),
    ).toBeInTheDocument();

    await user.click(screen.getByLabelText("Viewing annotator"));
    await user.click(screen.getByRole("option", { name: "Kartik (you)" }));

    expect(onViewingAnnotatorChange).toHaveBeenCalledWith("user-1");
  });

  it("does not render the old bottom discussion panel in the label form", () => {
    render(
      <LabelPanel
        labels={[]}
        annotations={[]}
        reviewComments={[
          {
            id: "comment-1",
            action: "comment",
            comment: "This is a side discussion.",
            reviewer_name: "Reviewer One",
          },
          {
            id: "comment-2",
            action: "request_changes",
            comment: "This blocks approval.",
            reviewer_name: "Reviewer One",
          },
        ]}
        onSubmit={vi.fn()}
        queueId="queue-1"
        itemId="item-1"
      />,
    );

    expect(
      screen.queryByText("This is a side discussion."),
    ).not.toBeInTheDocument();
    expect(screen.getByText("This blocks approval.")).toBeInTheDocument();
    expect(screen.queryByText("Discussion")).not.toBeInTheDocument();
  });

  it("summarizes open reviewer feedback before the label form", () => {
    render(
      <LabelPanel
        labels={[]}
        annotations={[]}
        reviewComments={[
          {
            id: "feedback-1",
            action: "request_changes",
            blocking: true,
            thread_status: "open",
            comment: "@[Narda](user:user-2) needs another pass.",
            reviewer_name: "Reviewer One",
            label_name: "sentiment",
            target_annotator_id: "user-1",
          },
        ]}
        currentUserId="user-1"
        onSubmit={vi.fn()}
        queueId="queue-1"
        itemId="item-1"
      />,
    );

    expect(screen.getByText("Feedback to address")).toBeInTheDocument();
    expect(screen.getByText("@Narda needs another pass.")).toBeInTheDocument();
    expect(screen.getByText("sentiment")).toBeInTheDocument();
  });

  it("treats legacy threadless request-change feedback as actionable", () => {
    render(
      <LabelPanel
        labels={[
          {
            id: "ql-1",
            label_id: "label-1",
            name: "sentiment",
            type: "text",
            settings: {},
          },
        ]}
        annotations={[]}
        reviewComments={[
          {
            id: "legacy-feedback-1",
            action: "request_changes",
            blocking: true,
            thread_status: null,
            label_id: "label-1",
            label_name: "sentiment",
            comment: "Legacy feedback still needs work.",
            reviewer_name: "Reviewer One",
            target_annotator_id: "user-1",
          },
        ]}
        currentUserId="user-1"
        onSubmit={vi.fn()}
        queueId="queue-1"
        itemId="item-1"
      />,
    );

    expect(screen.getByText("Feedback to address")).toBeInTheDocument();
    expect(
      screen.getAllByText("Legacy feedback still needs work.").length,
    ).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// AnnotationComparisonPanel
// ---------------------------------------------------------------------------
describe("AnnotationComparisonPanel", () => {
  const labels = [
    {
      id: "ql-1",
      label_id: "label-1",
      name: "thumbs",
      type: "thumbs_up_down",
      settings: {},
    },
    {
      id: "ql-2",
      label_id: "label-2",
      name: "cat",
      type: "categorical",
      settings: { multi_choice: true, options: ["1", "2"] },
    },
  ];

  const annotators = [
    {
      user_id: "user-1",
      name: "Kartik",
      email: "kartik.nvj@futureagi.com",
    },
    {
      user_id: "user-2",
      name: "Narda",
      email: "narda@example.com",
    },
  ];

  it("shows all annotators side by side with disagreement and notes", () => {
    render(
      <AnnotationComparisonPanel
        labels={labels}
        annotators={annotators}
        currentUserId="user-1"
        viewingAnnotatorId={ALL_ANNOTATORS}
        queueId="queue-1"
        itemId="item-1"
        annotations={[
          {
            id: "ann-1",
            annotator: "user-1",
            annotator_name: "Kartik",
            annotator_email: "kartik.nvj@futureagi.com",
            label_id: "label-1",
            label_type: "thumbs_up_down",
            value: { value: "up" },
            notes: "kartik note",
          },
          {
            id: "ann-2",
            annotator: "user-2",
            annotator_name: "Narda",
            annotator_email: "narda@example.com",
            label_id: "label-1",
            label_type: "thumbs_up_down",
            value: { value: "down" },
            notes: "narda note",
          },
        ]}
        spanNotes={[
          {
            id: "note-1",
            annotator: "narda@example.com",
            notes: "whole item note",
          },
        ]}
      />,
    );

    expect(screen.getByText("All annotators")).toBeInTheDocument();
    expect(screen.getByText("Score matrix")).toBeInTheDocument();
    expect(screen.getAllByText("Kartik (you)").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Narda").length).toBeGreaterThan(0);
    expect(screen.getByText("Disagreement")).toBeInTheDocument();
    expect(screen.getByText(/kartik note/)).toBeInTheDocument();
    expect(screen.getByText(/narda note/)).toBeInTheDocument();
    expect(screen.getByText("whole item note")).toBeInTheDocument();
  });

  it("filters comparison rows and item notes when one annotator is selected", () => {
    render(
      <AnnotationComparisonPanel
        labels={labels}
        annotators={annotators}
        currentUserId="user-1"
        viewingAnnotatorId="user-2"
        queueId="queue-1"
        itemId="item-1"
        annotations={[
          {
            id: "ann-1",
            annotator: "user-1",
            annotator_name: "Kartik",
            annotator_email: "kartik.nvj@futureagi.com",
            label_id: "label-1",
            label_type: "thumbs_up_down",
            value: { value: "up" },
            notes: "kartik note",
          },
          {
            id: "ann-2",
            annotator: "user-2",
            annotator_name: "Narda",
            annotator_email: "narda@example.com",
            label_id: "label-1",
            label_type: "thumbs_up_down",
            value: { value: "down" },
            notes: "narda note",
          },
        ]}
        spanNotes={[
          {
            id: "note-1",
            annotator_id: "user-1",
            annotator: "kartik.nvj@futureagi.com",
            notes: "kartik whole item note",
          },
          {
            id: "note-2",
            annotator_id: "user-2",
            annotator: "narda@example.com",
            notes: "narda whole item note",
          },
        ]}
      />,
    );

    expect(screen.getByText(/Showing only Narda/)).toBeInTheDocument();
    expect(screen.getByText("Answer review")).toBeInTheDocument();
    expect(screen.queryByText("Score matrix")).not.toBeInTheDocument();
    expect(screen.getAllByText("Narda").length).toBeGreaterThan(0);
    expect(screen.queryByText("Disagreement")).not.toBeInTheDocument();
    expect(screen.queryByText(/kartik note/)).not.toBeInTheDocument();
    expect(screen.getByText(/narda note/)).toBeInTheDocument();
    expect(
      screen.queryByText("kartik whole item note"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("narda whole item note")).toBeInTheDocument();
  });

  it("emits single annotator selection from the comparison switcher", async () => {
    const user = userEvent.setup();
    const onViewingAnnotatorChange = vi.fn();

    render(
      <AnnotationComparisonPanel
        labels={labels}
        annotators={annotators}
        currentUserId="user-1"
        viewingAnnotatorId={ALL_ANNOTATORS}
        onViewingAnnotatorChange={onViewingAnnotatorChange}
        queueId="queue-1"
        itemId="item-1"
      />,
    );

    await user.click(
      within(
        screen.getByRole("group", { name: "Viewing annotator" }),
      ).getByRole("button", { name: "Narda" }),
    );

    expect(onViewingAnnotatorChange).toHaveBeenCalledWith("user-2");
  });

  it("keeps the score matrix usable when many annotators are present", () => {
    const manyAnnotators = Array.from({ length: 7 }, (_, index) => ({
      user_id: `user-${index + 1}`,
      name: `Reviewer ${index + 1}`,
      email: `reviewer-${index + 1}@example.com`,
    }));

    render(
      <AnnotationComparisonPanel
        labels={labels}
        annotators={manyAnnotators}
        currentUserId="user-1"
        viewingAnnotatorId={ALL_ANNOTATORS}
        queueId="queue-1"
        itemId="item-1"
      />,
    );

    expect(screen.getByText("Score matrix")).toBeInTheDocument();
    expect(screen.getByText("7 annotators")).toBeInTheDocument();
    expect(screen.getByText("Scroll")).toBeInTheDocument();
    expect(screen.getAllByText("Reviewer 7").length).toBeGreaterThan(0);
  });

  it("lets reviewer discussions mention non-annotator queue members", async () => {
    const user = userEvent.setup();
    render(
      <CollaborationDrawer
        open
        onClose={vi.fn()}
        labels={labels}
        members={[
          ...annotators.slice(0, 1),
          {
            user_id: "reviewer-1",
            name: "QA Reviewer",
            email: "qa-reviewer@example.com",
            roles: ["reviewer"],
          },
        ]}
        queueId="queue-1"
        itemId="item-1"
        itemLabel="trace item-1"
        canTargetMembers
      />,
    );

    await user.type(screen.getByLabelText("Comment"), "@qa");

    expect(await screen.findByText("@QA Reviewer")).toBeInTheDocument();
  });

  it("blocks comment actions for users who are not queue members", () => {
    render(
      <CollaborationDrawer
        open
        onClose={vi.fn()}
        labels={labels}
        members={annotators.slice(0, 1)}
        queueId="queue-1"
        itemId="item-1"
        itemLabel="trace item-1"
        canComment={false}
        threads={[
          {
            id: "thread-1",
            action: "comment",
            status: "open",
            comments: [
              {
                id: "comment-1",
                action: "comment",
                comment: "Visible context only.",
                reviewer_name: "Reviewer One",
              },
            ],
          },
        ]}
      />,
    );

    expect(
      screen.getByText("Only queue members can comment on this item."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Comment")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reply" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Resolve thread" }),
    ).toBeDisabled();
  });

  it("creates a drawer comment from typed label and person tags", async () => {
    const user = userEvent.setup();
    mockCreateDiscussionComment.mockClear();

    render(
      <CollaborationDrawer
        open
        onClose={vi.fn()}
        item={{ id: "item-1", source_type: "trace" }}
        labels={labels}
        members={[
          ...annotators.slice(0, 1),
          {
            user_id: "reviewer-1",
            name: "QA Reviewer",
            email: "qa-reviewer@example.com",
            roles: ["reviewer"],
          },
        ]}
        queueId="queue-1"
        itemId="item-1"
        itemLabel="trace item-1"
        canTargetMembers
      />,
    );

    expect(
      screen.queryByRole("button", { name: "#thumbs" }),
    ).not.toBeInTheDocument();
    await user.type(
      screen.getByLabelText("Comment"),
      "#thumbs @QA Reviewer please check this",
    );
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(mockCreateDiscussionComment).toHaveBeenCalledWith(
      expect.objectContaining({
        queueId: "queue-1",
        itemId: "item-1",
        labelId: "label-1",
        targetAnnotatorId: "reviewer-1",
        mentionedUserIds: ["reviewer-1"],
      }),
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
    expect(mockCreateDiscussionComment.mock.calls[0][0].comment).toContain(
      "@QA Reviewer",
    );
  });

  it("does not swallow Enter when typed drawer tags have no suggestion", async () => {
    const user = userEvent.setup();

    render(
      <CollaborationDrawer
        open
        onClose={vi.fn()}
        labels={labels}
        members={annotators}
        queueId="queue-1"
        itemId="item-1"
        itemLabel="trace item-1"
        canTargetMembers
      />,
    );

    const commentField = screen.getByLabelText("Comment");
    await user.type(commentField, "#missing{Enter}next");
    expect(commentField).toHaveValue("#missing\nnext");

    await user.clear(commentField);
    await user.type(commentField, "@ghost{Enter}next");
    expect(commentField).toHaveValue("@ghost\nnext");
  });

  it("keeps threadless comments when their id matches a real thread id", () => {
    render(
      <CollaborationDrawer
        open
        onClose={vi.fn()}
        labels={labels}
        members={annotators}
        queueId="queue-1"
        itemId="item-1"
        itemLabel="trace item-1"
        threads={[
          {
            id: "comment-1",
            action: "comment",
            status: "open",
            comments: [
              {
                id: "nested-comment-1",
                action: "comment",
                comment: "Real thread context.",
                reviewer_name: "Reviewer One",
              },
            ],
          },
        ]}
        comments={[
          {
            id: "comment-1",
            action: "comment",
            comment: "Legacy threadless context.",
            reviewer_name: "Reviewer Two",
          },
        ]}
      />,
    );

    expect(screen.getByText("Real thread context.")).toBeInTheDocument();
    expect(screen.getByText("Legacy threadless context.")).toBeInTheDocument();
  });

  it("closes the persistent comments drawer with Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    render(
      <CollaborationDrawer
        open
        onClose={onClose}
        labels={labels}
        members={annotators}
        queueId="queue-1"
        itemId="item-1"
        itemLabel="trace item-1"
      />,
    );

    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("dismisses inline suggestions before Escape closes the comments drawer", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();

    render(
      <CollaborationDrawer
        open
        onClose={onClose}
        labels={labels}
        members={annotators}
        queueId="queue-1"
        itemId="item-1"
        itemLabel="trace item-1"
      />,
    );

    await user.type(screen.getByLabelText("Comment"), "@");
    expect(
      await screen.findByRole("listbox", { name: "Mention suggestions" }),
    ).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(
      screen.queryByRole("listbox", { name: "Mention suggestions" }),
    ).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("supports replying, resolving, reopening, and reacting in drawer threads", async () => {
    const user = userEvent.setup();
    mockCreateDiscussionComment.mockClear();
    mockResolveDiscussionThread.mockClear();
    mockReopenDiscussionThread.mockClear();
    mockToggleDiscussionReaction.mockClear();
    const onFocusScope = vi.fn();

    render(
      <CollaborationDrawer
        open
        onClose={vi.fn()}
        labels={labels}
        members={annotators.slice(0, 1)}
        queueId="queue-1"
        itemId="item-1"
        itemLabel="trace item-1"
        threads={[
          {
            id: "thread-1",
            action: "comment",
            status: "open",
            created_at: "2025-01-01T12:00:00Z",
            label_id: "label-1",
            label_name: "thumbs",
            comments: [
              {
                id: "comment-1",
                action: "comment",
                comment: "Please review #thumbs score.",
                reviewer_name: "Reviewer One",
                created_at: "2025-01-01T12:01:00Z",
                reactions: [{ emoji: "👍", count: 1 }],
              },
            ],
          },
          {
            id: "thread-2",
            action: "comment",
            status: "resolved",
            created_at: "2025-01-01T12:00:00Z",
            resolved_at: "2025-01-01T12:05:00Z",
            comments: [
              {
                id: "comment-2",
                action: "comment",
                comment: "Resolved context.",
                reviewer_name: "Reviewer One",
                created_at: "2025-01-01T12:02:00Z",
              },
            ],
          },
        ]}
        onFocusScope={onFocusScope}
      />,
    );

    expect(screen.getByText("1 active")).toBeInTheDocument();
    expect(screen.getAllByText("Resolved").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/5 minutes ago/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Created 5 minutes ago/).length).toBeGreaterThan(
      0,
    );
    expect(screen.getByText(/Resolved 5 minutes ago/)).toBeInTheDocument();
    expect(screen.getByText("Open threads")).toBeInTheDocument();
    expect(screen.getByText("Resolved threads")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "#thumbs" }));
    expect(onFocusScope).toHaveBeenCalledWith({
      labelId: "label-1",
      targetAnnotatorId: "",
    });
    await user.click(screen.getByRole("button", { name: "Focus #thumbs" }));
    expect(onFocusScope).toHaveBeenLastCalledWith({
      labelId: "label-1",
      targetAnnotatorId: "",
    });

    await user.click(screen.getByRole("button", { name: "Reply" }));
    await user.type(screen.getAllByLabelText("Comment").at(-1), "done");
    await user.click(screen.getAllByRole("button", { name: "Reply" }).at(-1));
    expect(mockCreateDiscussionComment).toHaveBeenCalledWith(
      expect.objectContaining({ threadId: "thread-1", comment: "done" }),
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );

    await user.click(screen.getByRole("button", { name: "Resolve thread" }));
    expect(mockResolveDiscussionThread).toHaveBeenCalledWith({
      queueId: "queue-1",
      itemId: "item-1",
      threadId: "thread-1",
    });

    await user.click(screen.getByRole("button", { name: "Reopen thread" }));
    expect(mockReopenDiscussionThread).toHaveBeenCalledWith({
      queueId: "queue-1",
      itemId: "item-1",
      threadId: "thread-2",
    });

    await user.click(
      screen.getAllByRole("button", { name: "Add reaction" })[0],
    );
    await user.click(
      await screen.findByRole("button", { name: "React with 🚀 Ship it" }),
    );
    expect(mockToggleDiscussionReaction).toHaveBeenCalledWith({
      queueId: "queue-1",
      itemId: "item-1",
      commentId: "comment-1",
      emoji: "🚀",
    });
  });

  it("keeps resolved threads read-only until reopened", async () => {
    const user = userEvent.setup();
    mockReopenDiscussionThread.mockClear();

    render(
      <CollaborationDrawer
        open
        onClose={vi.fn()}
        labels={labels}
        members={annotators}
        queueId="queue-1"
        itemId="item-1"
        itemLabel="trace item-1"
        threads={[
          {
            id: "thread-open",
            action: "comment",
            status: "open",
            comments: [
              {
                id: "comment-open",
                action: "comment",
                comment: "Active context.",
                reviewer_name: "Reviewer One",
              },
            ],
          },
          {
            id: "thread-resolved",
            action: "comment",
            status: "resolved",
            comments: [
              {
                id: "comment-resolved",
                action: "comment",
                comment: "Resolved context.",
                reviewer_name: "Reviewer One",
              },
            ],
          },
        ]}
      />,
    );

    expect(screen.getByText("Open threads")).toBeInTheDocument();
    expect(screen.getByText("Resolved threads")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Reply" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Reopen thread" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Reopen thread" }));
    expect(mockReopenDiscussionThread).toHaveBeenCalledWith({
      queueId: "queue-1",
      itemId: "item-1",
      threadId: "thread-resolved",
    });
  });

  it("shows resolve and reopen loading only on the affected thread", () => {
    mockDiscussionMutationState.resolve = {
      isPending: true,
      variables: { threadId: "thread-open-1" },
    };
    mockDiscussionMutationState.reopen = {
      isPending: true,
      variables: { threadId: "thread-resolved-2" },
    };

    render(
      <CollaborationDrawer
        open
        onClose={vi.fn()}
        labels={labels}
        members={annotators}
        queueId="queue-1"
        itemId="item-1"
        itemLabel="trace item-1"
        threads={[
          {
            id: "thread-open-1",
            action: "comment",
            status: "open",
            comments: [
              { id: "comment-open-1", action: "comment", comment: "A" },
            ],
          },
          {
            id: "thread-open-2",
            action: "comment",
            status: "open",
            comments: [
              { id: "comment-open-2", action: "comment", comment: "B" },
            ],
          },
          {
            id: "thread-resolved-1",
            action: "comment",
            status: "resolved",
            comments: [
              { id: "comment-resolved-1", action: "comment", comment: "C" },
            ],
          },
          {
            id: "thread-resolved-2",
            action: "comment",
            status: "resolved",
            comments: [
              { id: "comment-resolved-2", action: "comment", comment: "D" },
            ],
          },
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: "Resolving..." })).toBeDisabled();
    expect(
      within(screen.getByRole("button", { name: "Resolving..." })).getByRole(
        "progressbar",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Resolve thread" }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: "Reopening..." })).toBeDisabled();
    expect(
      within(screen.getByRole("button", { name: "Reopening..." })).getByRole(
        "progressbar",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reopen thread" })).toBeEnabled();
  });

  it("shows reaction loading only on the clicked comment", () => {
    mockDiscussionMutationState.reaction = {
      isPending: true,
      variables: { commentId: "comment-1" },
    };

    render(
      <CollaborationDrawer
        open
        onClose={vi.fn()}
        labels={labels}
        members={annotators}
        queueId="queue-1"
        itemId="item-1"
        itemLabel="trace item-1"
        threads={[
          {
            id: "thread-1",
            action: "comment",
            status: "open",
            comments: [
              { id: "comment-1", action: "comment", comment: "First" },
              { id: "comment-2", action: "comment", comment: "Second" },
            ],
          },
        ]}
      />,
    );

    const updatingReactionButton = screen.getByRole("button", {
      name: "Updating reaction",
    });
    expect(updatingReactionButton).toBeDisabled();
    expect(
      within(updatingReactionButton).getByRole("progressbar"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add reaction" })).toBeEnabled();
  });

  it("shows comment submit loading on the active composer", () => {
    mockDiscussionMutationState.create = {
      isPending: true,
      variables: undefined,
    };

    render(
      <CollaborationDrawer
        open
        onClose={vi.fn()}
        labels={labels}
        members={annotators}
        queueId="queue-1"
        itemId="item-1"
        itemLabel="trace item-1"
      />,
    );

    const sendButton = screen.getByRole("button", { name: "Send" });
    expect(sendButton).toBeDisabled();
    expect(within(sendButton).getByRole("progressbar")).toBeInTheDocument();
  });

  it("treats reopened threads as open action items", () => {
    render(
      <CollaborationDrawer
        open
        onClose={vi.fn()}
        labels={labels}
        members={annotators}
        queueId="queue-1"
        itemId="item-1"
        itemLabel="trace item-1"
        threads={[
          {
            id: "thread-reopened",
            action: "comment",
            status: "reopened",
            comments: [
              {
                id: "comment-reopened",
                action: "comment",
                comment: "Needs another look.",
                reviewer_name: "Reviewer One",
              },
            ],
          },
        ]}
      />,
    );

    expect(screen.getByText("1 active")).toBeInTheDocument();
    expect(screen.getByText("Reopened")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Resolve thread" }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Reopen thread" }),
    ).not.toBeInTheDocument();
  });

  it("submits reviewer feedback through approve and request-changes actions", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    const onReject = vi.fn();

    render(
      <AnnotationComparisonPanel
        labels={labels}
        annotators={annotators}
        currentUserId="user-1"
        viewingAnnotatorId={ALL_ANNOTATORS}
        queueId="queue-1"
        itemId="item-1"
        showReviewActions
        onApprove={onApprove}
        onReject={onReject}
      />,
    );

    await user.type(
      screen.getByLabelText("Whole item feedback"),
      "needs a clearer label note",
    );
    await user.click(
      screen.getByRole("button", { name: /feedback for thumbs \/ narda/i }),
    );
    await user.type(
      screen.getByLabelText("Feedback for thumbs / Narda"),
      "thumbs value is wrong",
    );
    await user.click(screen.getByRole("button", { name: /request changes/i }));

    expect(onReject).toHaveBeenCalledWith({
      notes: "needs a clearer label note",
      labelComments: [
        {
          label_id: "label-1",
          target_annotator_id: "user-2",
          comment: "thumbs value is wrong",
        },
      ],
    });
    expect(onApprove).not.toHaveBeenCalled();
  });

  it("keeps approve separate from request-change drafts", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();

    render(
      <AnnotationComparisonPanel
        labels={labels}
        annotators={annotators}
        currentUserId="user-1"
        viewingAnnotatorId={ALL_ANNOTATORS}
        queueId="queue-1"
        itemId="item-1"
        showReviewActions
        onApprove={onApprove}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: /feedback for thumbs \/ narda/i }),
    );
    await user.type(
      screen.getByLabelText("Feedback for thumbs / Narda"),
      "thumbs value is wrong",
    );

    expect(screen.getByRole("button", { name: /approve/i })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Clear" }));
    await user.click(screen.getByRole("button", { name: /approve/i }));

    expect(onApprove).toHaveBeenCalledWith({ notes: "", labelComments: [] });
  });

  it("can collect multiple targeted reviewer comments before requesting changes", async () => {
    const user = userEvent.setup();
    const onReject = vi.fn();

    render(
      <AnnotationComparisonPanel
        item={{ id: "item-1", source_type: "trace" }}
        labels={labels}
        annotators={annotators}
        currentUserId="user-1"
        viewingAnnotatorId={ALL_ANNOTATORS}
        queueId="queue-1"
        itemId="item-1"
        showReviewActions
        onReject={onReject}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: /feedback for thumbs \/ narda/i }),
    );
    expect(screen.getAllByText("trace item-1").length).toBeGreaterThan(0);
    await user.type(
      screen.getByLabelText("Feedback for thumbs / Narda"),
      "thumbs should be yes",
    );

    await user.click(
      screen.getByRole("button", { name: /feedback for cat \/ kartik/i }),
    );
    await user.type(
      screen.getByLabelText("Feedback for cat / Kartik"),
      "cat option is missing",
    );

    expect(screen.getByText("Feedback to send")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /request changes/i }));

    expect(onReject).toHaveBeenCalledWith({
      notes: "",
      labelComments: [
        {
          label_id: "label-1",
          target_annotator_id: "user-2",
          comment: "thumbs should be yes",
        },
        {
          label_id: "label-2",
          target_annotator_id: "user-1",
          comment: "cat option is missing",
        },
      ],
    });
  });

  it("reports reviewer draft dirty state to the workspace", async () => {
    const user = userEvent.setup();
    const onDirtyChange = vi.fn();

    render(
      <AnnotationComparisonPanel
        labels={labels}
        annotators={annotators}
        currentUserId="user-1"
        viewingAnnotatorId={ALL_ANNOTATORS}
        queueId="queue-1"
        itemId="item-1"
        showReviewActions
        onDirtyChange={onDirtyChange}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: /feedback for thumbs \/ narda/i }),
    );
    await user.type(
      screen.getByLabelText("Feedback for thumbs / Narda"),
      "please fix this score",
    );

    await waitFor(() => {
      expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    });
  });

  it("clears reviewer drafts after switching annotator scope", async () => {
    const user = userEvent.setup();
    const onDirtyChange = vi.fn();

    const { rerender } = render(
      <AnnotationComparisonPanel
        labels={labels}
        annotators={annotators}
        currentUserId="user-1"
        viewingAnnotatorId={ALL_ANNOTATORS}
        queueId="queue-1"
        itemId="item-1"
        showReviewActions
        onDirtyChange={onDirtyChange}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: /feedback for thumbs \/ narda/i }),
    );
    await user.type(
      screen.getByLabelText("Feedback for thumbs / Narda"),
      "please fix this score",
    );
    expect(screen.getByText("Feedback to send")).toBeInTheDocument();

    rerender(
      <AnnotationComparisonPanel
        labels={labels}
        annotators={annotators}
        currentUserId="user-1"
        viewingAnnotatorId="user-1"
        queueId="queue-1"
        itemId="item-1"
        showReviewActions
        onDirtyChange={onDirtyChange}
      />,
    );

    await waitFor(() => {
      expect(screen.queryByText("Feedback to send")).not.toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: /request changes/i }),
    ).toBeDisabled();
  });

  it("separates open requested changes from addressed and resolved review activity", () => {
    render(
      <AnnotationComparisonPanel
        labels={labels}
        annotators={annotators}
        currentUserId="user-1"
        viewingAnnotatorId={ALL_ANNOTATORS}
        queueId="queue-1"
        itemId="item-1"
        reviewComments={[
          {
            id: "comment-open",
            action: "request_changes",
            blocking: false,
            thread_status: "open",
            comment: "Open score issue",
            reviewer_name: "Reviewer One",
          },
          {
            id: "comment-addressed",
            action: "request_changes",
            blocking: false,
            thread_status: "addressed",
            comment: "Addressed score issue",
            reviewer_name: "Reviewer One",
          },
          {
            id: "comment-resolved",
            action: "request_changes",
            blocking: false,
            thread_status: "resolved",
            comment: "Resolved score issue",
            reviewer_name: "Reviewer One",
          },
          {
            id: "comment-legacy",
            action: "request_changes",
            blocking: false,
            thread_status: null,
            comment: "Legacy threadless issue",
            reviewer_name: "Reviewer One",
          },
        ]}
      />,
    );

    expect(screen.getByText("Open requested changes")).toBeInTheDocument();
    expect(screen.getByText("Open score issue")).toBeInTheDocument();
    expect(screen.getByText("Legacy threadless issue")).toBeInTheDocument();
    expect(screen.getAllByText("Addressed score issue").length).toBeGreaterThan(
      0,
    );
    expect(screen.getAllByText("Resolved score issue").length).toBeGreaterThan(
      0,
    );
  });
});

// ---------------------------------------------------------------------------
// DiscussionPanel
// ---------------------------------------------------------------------------
describe("DiscussionPanel", () => {
  it("submits scoped comments with mentioned queue members", async () => {
    const user = userEvent.setup();
    mockCreateDiscussionComment.mockClear();

    render(
      <DiscussionPanel
        queueId="queue-1"
        itemId="item-1"
        labels={[{ label_id: "label-1", name: "thumbs" }]}
        members={[
          {
            user_id: "user-2",
            name: "Narda",
            email: "narda@example.com",
          },
        ]}
        comments={[]}
      />,
    );

    await user.click(
      within(screen.getByRole("group", { name: "Comment scope" })).getByRole(
        "button",
        { name: "thumbs" },
      ),
    );
    await user.type(screen.getByLabelText("Comment"), "@Nar");
    await user.click(screen.getByRole("option", { name: /@Narda/i }));
    await user.type(screen.getByLabelText("Comment"), "Can you verify this?");
    await user.click(screen.getByRole("button", { name: /add comment/i }));

    expect(mockCreateDiscussionComment).toHaveBeenCalledWith(
      {
        queueId: "queue-1",
        itemId: "item-1",
        comment: "@Narda Can you verify this?",
        labelId: "label-1",
        mentionedUserIds: ["user-2"],
      },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("can scope a discussion comment to one annotator without a dropdown", async () => {
    const user = userEvent.setup();
    mockCreateDiscussionComment.mockClear();

    render(
      <DiscussionPanel
        queueId="queue-1"
        itemId="item-1"
        labels={[{ label_id: "label-1", name: "thumbs" }]}
        members={[
          {
            user_id: "user-2",
            name: "Narda",
            email: "narda@example.com",
          },
        ]}
        comments={[]}
        canTargetMembers
      />,
    );

    await user.click(
      within(screen.getByRole("group", { name: "Comment audience" })).getByRole(
        "button",
        { name: "@Narda" },
      ),
    );
    await user.type(screen.getByLabelText("Comment"), "Please re-check this.");
    await user.click(screen.getByRole("button", { name: /add comment/i }));

    expect(mockCreateDiscussionComment).toHaveBeenCalledWith(
      expect.objectContaining({
        queueId: "queue-1",
        itemId: "item-1",
        comment: "Please re-check this.",
        targetAnnotatorId: "user-2",
        mentionedUserIds: ["user-2"],
      }),
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("detects manually typed @mentions without a separate picker field", async () => {
    const user = userEvent.setup();
    mockCreateDiscussionComment.mockClear();

    render(
      <DiscussionPanel
        queueId="queue-1"
        itemId="item-1"
        labels={[]}
        members={[
          {
            user_id: "user-2",
            name: "Narda",
            email: "narda@example.com",
          },
        ]}
        comments={[]}
      />,
    );

    await user.type(
      screen.getByLabelText("Comment"),
      "@narda@example.com please check this",
    );
    await user.click(screen.getByRole("button", { name: /add comment/i }));

    expect(mockCreateDiscussionComment).toHaveBeenCalledWith(
      expect.objectContaining({
        comment: "@narda@example.com please check this",
        mentionedUserIds: ["user-2"],
      }),
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("does not swallow Enter when typed discussion mentions have no suggestion", async () => {
    const user = userEvent.setup();

    render(
      <DiscussionPanel
        queueId="queue-1"
        itemId="item-1"
        labels={[]}
        members={[
          {
            user_id: "user-2",
            name: "Narda",
            email: "narda@example.com",
          },
        ]}
        comments={[]}
      />,
    );

    const commentField = screen.getByLabelText("Comment");
    await user.type(commentField, "@missing{Enter}next");

    expect(commentField).toHaveValue("@missing\nnext");
  });

  it("does not submit stale mentions after the text is edited", async () => {
    const user = userEvent.setup();
    mockCreateDiscussionComment.mockClear();

    render(
      <DiscussionPanel
        queueId="queue-1"
        itemId="item-1"
        labels={[]}
        members={[
          {
            user_id: "user-2",
            name: "Narda",
            email: "narda@example.com",
          },
        ]}
        comments={[]}
      />,
    );

    const commentField = screen.getByLabelText("Comment");
    await user.type(commentField, "@Nar");
    await user.click(screen.getByRole("option", { name: /@Narda/i }));
    await user.clear(commentField);
    await user.type(commentField, "No mention here");
    await user.click(screen.getByRole("button", { name: /add comment/i }));

    expect(mockCreateDiscussionComment).toHaveBeenCalledWith(
      expect.objectContaining({
        comment: "No mention here",
        mentionedUserIds: [],
      }),
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("does not mention users whose aliases are only substrings", async () => {
    const user = userEvent.setup();
    mockCreateDiscussionComment.mockClear();

    render(
      <DiscussionPanel
        queueId="queue-1"
        itemId="item-1"
        labels={[]}
        members={[
          {
            user_id: "user-sam",
            name: "Sam",
            email: "sam@example.com",
          },
          {
            user_id: "user-samantha",
            name: "Samantha",
            email: "samantha@example.com",
          },
        ]}
        comments={[]}
      />,
    );

    await user.type(
      screen.getByLabelText("Comment"),
      "@samantha. please check this",
    );
    await user.click(screen.getByRole("button", { name: /add comment/i }));

    expect(mockCreateDiscussionComment).toHaveBeenCalledWith(
      expect.objectContaining({
        mentionedUserIds: ["user-samantha"],
      }),
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("renders mentioned people inline inside existing discussion comments", () => {
    render(
      <DiscussionPanel
        queueId="queue-1"
        itemId="item-1"
        labels={[]}
        members={[]}
        comments={[
          {
            id: "comment-1",
            action: "comment",
            comment: "@Narda please verify the score",
            reviewer_name: "Reviewer One",
            label_name: "thumbs",
            target_annotator_id: "user-2",
            target_annotator_name: "Narda",
            mentioned_users: [
              {
                id: "user-2",
                name: "Narda",
                email: "narda@example.com",
              },
            ],
          },
        ]}
      />,
    );

    expect(screen.getByText("Reviewer One")).toBeInTheDocument();
    expect(screen.getByText("thumbs / for Narda")).toBeInTheDocument();
    expect(screen.getAllByText("@Narda").length).toBeGreaterThan(0);
    expect(screen.getByText(/please verify the score/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AnnotateHeader
// ---------------------------------------------------------------------------
describe("AnnotateHeader", () => {
  it("renders queue name", () => {
    render(
      <AnnotateHeader
        queueName="My Queue"
        progress={{ total: 10, completed: 3 }}
        onBack={() => {}}
        onSkip={() => {}}
        isSkipping={false}
      />,
    );
    expect(screen.getByText("My Queue")).toBeInTheDocument();
  });

  it("renders progress", () => {
    render(
      <AnnotateHeader
        queueName="Q"
        progress={{ total: 10, completed: 3 }}
        onBack={() => {}}
        onSkip={() => {}}
        isSkipping={false}
      />,
    );
    expect(screen.getByText("3/10 (30%)")).toBeInTheDocument();
  });

  it("renders comments beside progress with the active action badge", async () => {
    const user = userEvent.setup();
    const onOpenComments = vi.fn();
    render(
      <AnnotateHeader
        queueName="Q"
        progress={{ total: 10, completed: 3 }}
        onBack={() => {}}
        onSkip={() => {}}
        isSkipping={false}
        onOpenComments={onOpenComments}
        commentBadgeCount={3}
        activeCommentCount={1}
        openFeedbackCount={2}
        addressedFeedbackCount={4}
        resolvedFeedbackCount={5}
      />,
    );

    const commentsButton = screen.getByRole("button", { name: /comments/i });
    expect(screen.getByText("3")).toBeInTheDocument();
    await user.hover(commentsButton);
    expect(await screen.findByText("1 active")).toBeInTheDocument();
    expect(screen.getByText("2 open")).toBeInTheDocument();

    await user.click(commentsButton);
    expect(onOpenComments).toHaveBeenCalledOnce();
  });

  it("keeps comments available in review mode without showing Skip", async () => {
    const user = userEvent.setup();
    const onOpenComments = vi.fn();
    render(
      <AnnotateHeader
        queueName="Q"
        progress={{ total: 10, completed: 3 }}
        onBack={() => {}}
        onSkip={() => {}}
        isSkipping={false}
        isReviewMode
        isSkipDisabled
        onOpenComments={onOpenComments}
        commentBadgeCount={1}
      />,
    );

    expect(
      screen.queryByRole("button", { name: /skip/i }),
    ).not.toBeInTheDocument();
    const commentsButton = screen.getByRole("button", { name: /comments/i });
    expect(commentsButton).toBeEnabled();

    await user.click(commentsButton);
    expect(onOpenComments).toHaveBeenCalledOnce();
  });

  it("renders Skip button", () => {
    render(
      <AnnotateHeader
        queueName="Q"
        progress={{}}
        onBack={() => {}}
        onSkip={() => {}}
        isSkipping={false}
      />,
    );
    expect(screen.getByRole("button", { name: /skip/i })).toBeInTheDocument();
  });

  it("calls onSkip when Skip button clicked", async () => {
    const user = userEvent.setup();
    const onSkip = vi.fn();
    render(
      <AnnotateHeader
        queueName="Q"
        progress={{}}
        onBack={() => {}}
        onSkip={onSkip}
        isSkipping={false}
      />,
    );
    await user.click(screen.getByRole("button", { name: /skip/i }));
    expect(onSkip).toHaveBeenCalledOnce();
  });

  it("disables Skip when isSkipping", () => {
    render(
      <AnnotateHeader
        queueName="Q"
        progress={{}}
        onBack={() => {}}
        onSkip={() => {}}
        isSkipping={true}
      />,
    );
    expect(screen.getByRole("button", { name: /skip/i })).toBeDisabled();
  });

  it("disables Skip when the item is locked", () => {
    render(
      <AnnotateHeader
        queueName="Q"
        progress={{}}
        onBack={() => {}}
        onSkip={() => {}}
        isSkipping={false}
        isSkipDisabled
      />,
    );
    expect(screen.getByRole("button", { name: /skip/i })).toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// AnnotateFooter
// ---------------------------------------------------------------------------
describe("AnnotateFooter", () => {
  it("renders position indicator", () => {
    render(
      <AnnotateFooter
        currentPosition={3}
        total={10}
        onPrev={() => {}}
        onNext={() => {}}
        hasPrev={true}
        hasNext={true}
      />,
    );
    expect(screen.getByText("Item 3 of 10")).toBeInTheDocument();
  });

  it("renders Previous and Next buttons", () => {
    render(
      <AnnotateFooter
        currentPosition={1}
        total={5}
        onPrev={() => {}}
        onNext={() => {}}
        hasPrev={false}
        hasNext={true}
      />,
    );
    expect(
      screen.getByRole("button", { name: /previous/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /next/i })).toBeInTheDocument();
  });

  it("disables Previous when hasPrev=false", () => {
    render(
      <AnnotateFooter
        currentPosition={1}
        total={5}
        onPrev={() => {}}
        onNext={() => {}}
        hasPrev={false}
        hasNext={true}
      />,
    );
    expect(screen.getByRole("button", { name: /previous/i })).toBeDisabled();
  });

  it("disables Next when hasNext=false", () => {
    render(
      <AnnotateFooter
        currentPosition={5}
        total={5}
        onPrev={() => {}}
        onNext={() => {}}
        hasPrev={true}
        hasNext={false}
      />,
    );
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
  });

  it("calls onPrev and onNext on click", async () => {
    const user = userEvent.setup();
    const onPrev = vi.fn();
    const onNext = vi.fn();
    render(
      <AnnotateFooter
        currentPosition={3}
        total={5}
        onPrev={onPrev}
        onNext={onNext}
        hasPrev={true}
        hasNext={true}
      />,
    );
    await user.click(screen.getByRole("button", { name: /previous/i }));
    expect(onPrev).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: /next/i }));
    expect(onNext).toHaveBeenCalledOnce();
  });
});

// ---------------------------------------------------------------------------
// AnnotationHistory
// ---------------------------------------------------------------------------
describe("AnnotationHistory", () => {
  it("returns null when itemId is falsy", () => {
    const { container } = render(
      <AnnotationHistory queueId="q-1" itemId={null} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders collapsed by default with annotation count", () => {
    render(<AnnotationHistory queueId="q-1" itemId="item-1" />);
    expect(screen.getByText(/ANNOTATION HISTORY/)).toBeInTheDocument();
  });

  it("shows 'No annotations yet' when expanded with no data", async () => {
    const user = userEvent.setup();
    render(<AnnotationHistory queueId="q-1" itemId="item-1" />);

    await user.click(screen.getByText(/ANNOTATION HISTORY/));
    expect(screen.getByText("No annotations yet")).toBeInTheDocument();
  });
});
