/**
 * Phase 2B-3C – Annotation workspace component tests.
 * Tests: LabelInput, AnnotateHeader, AnnotateFooter, AnnotationHistory
 */
/* eslint-disable react/prop-types */
import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import LabelInput from "../annotate/label-input";
import LabelPanel from "../annotate/label-panel";
import AnnotationComparisonPanel from "../annotate/annotation-comparison-panel";
import { ALL_ANNOTATORS } from "../annotate/annotation-view-mode";
import AnnotateHeader from "../annotate/annotate-header";
import AnnotateFooter from "../annotate/annotate-footer";
import AnnotationHistory from "../annotate/annotation-history";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/utils/format-time", () => ({
  fDateTime: () => "Jan 1, 2025 12:00",
}));

vi.mock("src/sections/common/CellMarkdown", () => ({
  default: ({ text }) => <div>{text}</div>,
}));

// Mock API hook for annotation history
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useItemAnnotations: vi.fn(() => ({ data: [] })),
}));

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

    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByRole("option", { name: "Kartik (you)" }));

    expect(onViewingAnnotatorChange).toHaveBeenCalledWith("user-1");
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
    expect(screen.getAllByText("Kartik (you)").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Narda").length).toBeGreaterThan(0);
    expect(screen.getByText("Disagreement")).toBeInTheDocument();
    expect(screen.getByText("Note: kartik note")).toBeInTheDocument();
    expect(screen.getByText("Note: narda note")).toBeInTheDocument();
    expect(screen.getByText("whole item note")).toBeInTheDocument();
  });

  it("emits single annotator selection from the comparison dropdown", async () => {
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

    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByRole("option", { name: "Narda" }));

    expect(onViewingAnnotatorChange).toHaveBeenCalledWith("user-2");
  });

  it("submits reviewer feedback through approve and reject actions", async () => {
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
      screen.getByLabelText("Reviewer feedback"),
      "needs a clearer label note",
    );
    await user.click(screen.getByRole("button", { name: /reject/i }));

    expect(onReject).toHaveBeenCalledWith("needs a clearer label note");
    expect(onApprove).not.toHaveBeenCalled();
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
