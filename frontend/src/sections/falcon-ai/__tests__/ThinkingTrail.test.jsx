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

// The flow the eval-build skill declares.
const PLAN = [
  "get_project",
  "get_eval_template_by_name",
  "get_project_eval_attributes",
  "get_trace_spans_by_type",
  "read_trace_span",
  "test_eval_template",
  "check_eval_config_exists",
  "create_custom_eval_config",
  "create_eval_task",
  "get_eval_task",
  "get_eval_task_logs",
];

const done = (tool, i) => ({
  call_id: `c${i}`,
  tool_name: tool,
  status: "completed",
});

describe("ThinkingTrail against a declared flow", () => {
  it("says where in the declared flow the live step is", () => {
    render(
      <ThinkingTrail
        toolCalls={[
          done("get_project", 1),
          done("get_eval_template_by_name", 2),
          done("get_project_eval_attributes", 3),
          done("get_trace_spans_by_type", 4),
          { call_id: "c5", tool_name: "read_trace_span", status: "running" },
        ]}
        plan={PLAN}
        isStreaming
      />,
    );
    expect(
      screen.getByText("Reading trace span · step 5 of 11"),
    ).toBeInTheDocument();
  });

  it("does not pretend an unplanned tool is a declared step", () => {
    render(
      <ThinkingTrail
        toolCalls={[
          done("get_project", 1),
          { call_id: "c2", tool_name: "search_docs", status: "running" },
        ]}
        plan={PLAN}
        isStreaming
      />,
    );
    expect(screen.getByText("Searching docs · extra step")).toBeInTheDocument();
  });

  it("says the tool is repeating rather than moving the flow on", () => {
    render(
      <ThinkingTrail
        toolCalls={[
          done("get_project", 1),
          done("read_trace_span", 2),
          { call_id: "c3", tool_name: "read_trace_span", status: "running" },
        ]}
        plan={PLAN}
        isStreaming
      />,
    );
    expect(
      screen.getByText("Reading trace span · step 5 of 11, again"),
    ).toBeInTheDocument();
  });

  it("counts the run against the flow when it finishes", () => {
    render(
      <ThinkingTrail
        toolCalls={[
          done("get_project", 1),
          done("get_eval_template_by_name", 2),
          done("search_docs", 3),
        ]}
        plan={PLAN}
      />,
    );
    expect(screen.getByText(/2 of 11 steps · 1 extra/)).toBeInTheDocument();
  });

  it("stays exactly as it was when the turn ran no skill", () => {
    render(<ThinkingTrail toolCalls={FINISHED} plan={[]} />);
    expect(screen.getByText(/3 steps/)).toBeInTheDocument();
    expect(screen.queryByText(/of 11/)).not.toBeInTheDocument();
  });

  it("claims no flow when the run matched none of it", () => {
    render(
      <ThinkingTrail
        toolCalls={[done("search_docs", 1), done("whoami", 2)]}
        plan={PLAN}
      />,
    );
    expect(screen.getByText(/2 steps/)).toBeInTheDocument();
    expect(screen.queryByText(/extra/)).not.toBeInTheDocument();
  });

  it("shows every call that ran, planned or not, on expand", () => {
    render(
      <ThinkingTrail
        toolCalls={[done("get_project", 1), done("search_docs", 2)]}
        plan={PLAN}
      />,
    );
    fireEvent.click(screen.getByText(/1 of 11 steps/));
    expect(screen.getByText("get_project")).toBeInTheDocument();
    expect(screen.getByText("search_docs")).toBeInTheDocument();
    expect(screen.getByText("extra")).toBeInTheDocument();
  });

  it("names the declared steps that have not run", () => {
    render(<ThinkingTrail toolCalls={[done("get_project", 1)]} plan={PLAN} />);
    fireEvent.click(screen.getByText(/1 of 11 steps/));
    expect(screen.getByText("Declared, not run")).toBeInTheDocument();
    expect(screen.getByText("Reading eval task logs")).toBeInTheDocument();
  });

  it("counts the whole turn, not one segment, when a trail is split", () => {
    const all = [
      done("get_project", 1),
      done("get_eval_template_by_name", 2),
      done("get_project_eval_attributes", 3),
    ];
    render(
      <ThinkingTrail toolCalls={all.slice(2)} planRun={all} plan={PLAN} />,
    );
    expect(screen.getByText(/3 of 11 steps/)).toBeInTheDocument();
  });
});
