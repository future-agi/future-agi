import PropTypes from "prop-types";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import ThinkingTrail from "../components/ThinkingTrail";

function MockIconify({ icon, ...props }) {
  return <span data-testid="iconify" data-icon={icon} {...props} />;
}

MockIconify.propTypes = { icon: PropTypes.string.isRequired };

vi.mock("src/components/iconify", () => ({ default: MockIconify }));

const FINISHED = [
  { call_id: "1", tool_name: "list_eval_templates", status: "completed" },
  {
    call_id: "2",
    tool_name: "create_eval_task",
    status: "error",
    result_summary: "**Error:** Invalid parameters: name Field required",
  },
  { call_id: "3", tool_name: "create_eval_task", status: "completed" },
];

describe("ThinkingTrail", () => {
  it("renders nothing without steps", () => {
    const { container } = render(<ThinkingTrail toolCalls={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("is one line, not one card per step", () => {
    render(<ThinkingTrail toolCalls={FINISHED} />);
    expect(screen.getByText(/3 steps/)).toBeInTheDocument();
    expect(screen.queryByText("list_eval_templates")).not.toBeInTheDocument();
  });

  it("shows the live tool while the turn runs", () => {
    render(
      <ThinkingTrail
        toolCalls={[
          { call_id: "1", tool_name: "search_traces", status: "running" },
        ]}
        isStreaming
      />,
    );
    expect(screen.getByText("Searching traces")).toBeInTheDocument();
  });

  it("does not count a recovered step as skipped", () => {
    render(<ThinkingTrail toolCalls={FINISHED} />);
    expect(screen.queryByText(/skipped/)).not.toBeInTheDocument();
  });

  it("says so when a step really did not return", () => {
    render(
      <ThinkingTrail
        toolCalls={[
          {
            call_id: "1",
            tool_name: "search_docs",
            status: "error",
            result_summary: "Documentation search is currently unavailable.",
          },
        ]}
      />,
    );
    expect(screen.getByText(/1 skipped/)).toBeInTheDocument();
  });

  it("opens the steps on click", () => {
    render(<ThinkingTrail toolCalls={FINISHED} />);
    fireEvent.click(screen.getByText(/3 steps/));
    expect(screen.getAllByText("create_eval_task")).toHaveLength(2);
  });
});
