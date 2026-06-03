import PropTypes from "prop-types";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
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
