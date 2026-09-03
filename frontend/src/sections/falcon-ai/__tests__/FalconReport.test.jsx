import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import FalconReport from "../components/FalconReport";
import AssistantMessage from "../components/AssistantMessage";
import downloadReportPdf from "../helpers/downloadReportPdf";

function MockIconify({ icon, ...props }) {
  return <span data-testid="iconify" data-icon={icon} {...props} />;
}

MockIconify.propTypes = { icon: PropTypes.string.isRequired };

vi.mock("src/components/iconify", () => ({ default: MockIconify }));
vi.mock("../helpers/downloadReportPdf", () => ({ default: vi.fn() }));

const REPORT = `# Eval Readiness

airline-support-agent, read-only

**Ready today, on a proven mapping.**

\`\`\`stats
164 | TRACES IN PROJECT
0 | EVALS RUNNING
\`\`\`

## 01 - The filter

|  | Evaluation | Column |
|---|---|---|
| 1 | \`airline-is-concise\` built in | \`output\` |
`;

describe("FalconReport", () => {
  it("renders nothing for an empty answer", () => {
    const { container } = render(<FalconReport content="" />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the title, the subject line and the finding", () => {
    render(<FalconReport content={REPORT} />);
    expect(screen.getByText("Eval Readiness")).toBeInTheDocument();
    expect(screen.getByText("airline-support-agent, read-only")).toBeInTheDocument();
    expect(screen.getByText("Ready today, on a proven mapping.")).toBeInTheDocument();
  });

  it("puts the headline figures on the page with their labels", () => {
    render(<FalconReport content={REPORT} />);
    expect(screen.getByText("164")).toBeInTheDocument();
    expect(screen.getByText("TRACES IN PROJECT")).toBeInTheDocument();
  });

  it("keeps the step number beside its heading", () => {
    render(<FalconReport content={REPORT} />);
    expect(screen.getByText("01")).toBeInTheDocument();
    expect(screen.getByText("The filter")).toBeInTheDocument();
  });

  it("badges the eval kind instead of printing it as score text", () => {
    const { container } = render(<FalconReport content={REPORT} />);
    const tag = container.querySelector(".tag");
    expect(tag).not.toBeNull();
    expect(tag.textContent).toBe("built in");
  });
});

describe("AssistantMessage report actions", () => {
  beforeEach(() => {
    downloadReportPdf.mockClear();
  });

  const openActions = (message) => {
    const { container } = render(<AssistantMessage message={message} />);
    fireEvent.mouseEnter(container.firstChild);
    return container;
  };

  it("offers the PDF only once the answer is a report", () => {
    openActions({ id: "a", content: "Hey! What would you like to do?" });
    expect(screen.queryByTitle("Download as PDF")).toBeNull();

    openActions({ id: "b", content: REPORT });
    expect(screen.getByTitle("Download as PDF")).toBeInTheDocument();
  });

  it("hands the answer to the renderer when the button is pressed", () => {
    openActions({ id: "c", content: REPORT });
    fireEvent.click(screen.getByTitle("Download as PDF"));
    expect(downloadReportPdf).toHaveBeenCalledWith(REPORT);
  });

  it("takes the answer out of the streamed text blocks, not the raw content", () => {
    openActions({
      id: "d",
      content: "",
      blocks: [
        { id: "1", type: "text", content: REPORT },
        { id: "2", type: "tool_call", toolCall: { call_id: "x", tool_name: "whoami" } },
      ],
    });
    fireEvent.click(screen.getByTitle("Download as PDF"));
    expect(downloadReportPdf).toHaveBeenCalledWith(REPORT);
  });
});
