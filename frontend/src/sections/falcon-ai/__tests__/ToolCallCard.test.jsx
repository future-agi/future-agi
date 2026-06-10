import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import useFalconStore from "../store/useFalconStore";
import ToolCallCard from "../components/ToolCallCard";

// Mock Iconify (renders a simple span)
function MockIconify({ icon, ...props }) {
  return <span data-testid="iconify" data-icon={icon} {...props} />;
}
MockIconify.propTypes = { icon: PropTypes.string.isRequired };
vi.mock("src/components/iconify", () => ({ default: MockIconify }));

// Mock TextBlock — render the full result as plain text so we can assert on it
vi.mock("../components/TextBlock", () => ({
  default: ({ content }) => <div data-testid="text-block">{content}</div>,
}));

const completedCall = {
  call_id: "tc_1",
  tool_name: "search_traces",
  tool_description: "Search the traces.",
  params: { query: "errors", limit: 20 },
  status: "completed",
  result_summary: "## Traces (5197)\nShowing 20 of 5197",
  result_full: "FULL_RESULT_BODY_marker",
  step: 1,
};

describe("ToolCallCard (compact)", () => {
  it("shows the real tool name with underscores swapped for spaces", () => {
    render(<ToolCallCard toolCall={completedCall} />);
    expect(screen.getByText("search traces")).toBeInTheDocument();
  });

  it("shows a single-line result hint when collapsed (no wide block)", () => {
    render(<ToolCallCard toolCall={completedCall} />);
    // firstLine() strips markdown and takes the first non-empty line
    expect(screen.getByText("Traces (5197)")).toBeInTheDocument();
  });

  it("is collapsed by default — full result is NOT rendered", () => {
    render(<ToolCallCard toolCall={completedCall} />);
    expect(
      screen.queryByText("FULL_RESULT_BODY_marker"),
    ).not.toBeInTheDocument();
  });

  it("expands the details (full result) when the row is clicked", () => {
    render(<ToolCallCard toolCall={completedCall} />);
    fireEvent.click(screen.getByText("search traces"));
    expect(screen.getByText("FULL_RESULT_BODY_marker")).toBeInTheDocument();
  });

  it("a running tool shows 'running…' and is not expandable", () => {
    render(
      <ToolCallCard
        toolCall={{
          call_id: "tc_2",
          tool_name: "list_datasets",
          status: "running",
        }}
      />,
    );
    expect(screen.getByText("list datasets")).toBeInTheDocument();
    expect(screen.getByText("running…")).toBeInTheDocument();
  });
});

describe("ToolCallCard execution-policy badge (unit, UX_UI 7.1)", () => {
  it("shows a 'write' badge for mutate tools", () => {
    render(
      <ToolCallCard
        toolCall={{ ...completedCall, execution_policy: "mutate" }}
      />,
    );
    expect(screen.getByText("write")).toBeInTheDocument();
  });

  it("shows a 'destructive' badge for destructive tools", () => {
    render(
      <ToolCallCard
        toolCall={{ ...completedCall, execution_policy: "destructive" }}
      />,
    );
    expect(screen.getByText("destructive")).toBeInTheDocument();
  });

  it("shows no badge for read tools (or when policy is absent)", () => {
    render(
      <ToolCallCard
        toolCall={{ ...completedCall, execution_policy: "read" }}
      />,
    );
    expect(screen.queryByText("write")).not.toBeInTheDocument();
    expect(screen.queryByText("destructive")).not.toBeInTheDocument();
  });
});

const confirmationCall = {
  call_id: "tc_9",
  tool_name: "delete_dataset",
  params: { dataset_id: "ds-1" },
  status: "confirmation_required",
  execution_policy: "destructive",
  confirmation: {
    token: "tok-123",
    tool_name: "delete_dataset",
    args: { dataset_id: "ds-1" },
    preview: 'Will permanently delete dataset "fraud-eval" (ds-1, 1,204 rows).',
    expires_at: "2026-06-10T12:15:00Z",
    policy: "destructive",
    undo_note: null,
  },
};

describe("ToolCallCard confirmation card (unit, destructive phase-1)", () => {
  beforeEach(() => {
    useFalconStore.getState().resetAll();
  });

  it("renders the inline confirm card with preview and irreversibility note", () => {
    render(
      <ToolCallCard toolCall={confirmationCall} onConfirmAction={vi.fn()} />,
    );
    expect(screen.getByText("Confirm destructive action")).toBeInTheDocument();
    expect(screen.getByText(/Falcon wants to run:/)).toBeInTheDocument();
    expect(
      screen.getByText(/Will permanently delete dataset "fraud-eval"/),
    ).toBeInTheDocument();
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();
    expect(screen.getByText("needs your confirmation")).toBeInTheDocument();
  });

  it("derives the confirm button label from the tool verb", () => {
    render(
      <ToolCallCard toolCall={confirmationCall} onConfirmAction={vi.fn()} />,
    );
    expect(
      screen.getByRole("button", { name: "Confirm delete" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("shows the undo note instead of 'cannot be undone' when one exists", () => {
    render(
      <ToolCallCard
        toolCall={{
          ...confirmationCall,
          confirmation: {
            ...confirmationCall.confirmation,
            undo_note: "Can be undone by re-creating the dataset.",
          },
        }}
        onConfirmAction={vi.fn()}
      />,
    );
    expect(
      screen.getByText("Can be undone by re-creating the dataset."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("This cannot be undone."),
    ).not.toBeInTheDocument();
  });

  it("Confirm sends (token, 'confirm') and disables both buttons", () => {
    const onConfirmAction = vi.fn();
    render(
      <ToolCallCard
        toolCall={confirmationCall}
        onConfirmAction={onConfirmAction}
      />,
    );
    const confirmBtn = screen.getByRole("button", { name: "Confirm delete" });
    fireEvent.click(confirmBtn);
    expect(onConfirmAction).toHaveBeenCalledExactlyOnceWith(
      "tok-123",
      "confirm",
    );
    expect(confirmBtn).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  it("Cancel sends (token, 'cancel')", () => {
    const onConfirmAction = vi.fn();
    render(
      <ToolCallCard
        toolCall={confirmationCall}
        onConfirmAction={onConfirmAction}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onConfirmAction).toHaveBeenCalledExactlyOnceWith(
      "tok-123",
      "cancel",
    );
  });

  it("disables the buttons while a stream is in flight (server would reject)", () => {
    useFalconStore.getState().setStreaming(true, "assistant-x");
    render(
      <ToolCallCard toolCall={confirmationCall} onConfirmAction={vi.fn()} />,
    );
    expect(
      screen.getByRole("button", { name: "Confirm delete" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  it.each([
    ["confirmed", /Approved — Falcon is proceeding/],
    ["cancelled", /Cancelled — no action was taken/],
    ["expired", /Confirmation expired/],
  ])(
    "resolution '%s' replaces the buttons with the outcome",
    (resolution, text) => {
      render(
        <ToolCallCard
          toolCall={{ ...confirmationCall, confirmation_status: resolution }}
          onConfirmAction={vi.fn()}
        />,
      );
      expect(screen.getByText(text)).toBeInTheDocument();
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    },
  );
});

describe("ToolCallCard undo hint (unit, executed destructive leg)", () => {
  beforeEach(() => {
    useFalconStore.getState().resetAll();
  });

  it("offers a one-click Undo that prefills the chat input via pendingPrompt", () => {
    render(
      <ToolCallCard
        toolCall={{
          ...completedCall,
          tool_name: "delete_dataset",
          undo: {
            note: "Can be restored from the captured snapshot.",
            prompt: "Undo the dataset deletion using the snapshot.",
          },
        }}
      />,
    );
    expect(
      screen.getByText("Can be restored from the captured snapshot."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(useFalconStore.getState().pendingPrompt).toBe(
      "Undo the dataset deletion using the snapshot.",
    );
  });

  it("shows no undo affordance without an undo payload", () => {
    render(<ToolCallCard toolCall={completedCall} />);
    expect(
      screen.queryByRole("button", { name: "Undo" }),
    ).not.toBeInTheDocument();
  });
});

describe("ToolCallCard result-preview truncation honesty", () => {
  // agent.py caps result_full at result_text[:2000] — the card must say so
  // instead of presenting a cut-off payload as if it were the whole result.
  it("notes the 2,000-character cap when result_full hits it", () => {
    render(
      <ToolCallCard
        toolCall={{ ...completedCall, result_full: "x".repeat(2000) }}
      />,
    );
    fireEvent.click(screen.getByText("search traces"));
    expect(
      screen.getByText(/Preview capped at 2,000 characters/),
    ).toBeInTheDocument();
  });

  it("shows no truncation note for results under the cap", () => {
    render(<ToolCallCard toolCall={completedCall} />);
    fireEvent.click(screen.getByText("search traces"));
    expect(screen.queryByText(/Preview capped/)).not.toBeInTheDocument();
  });
});
