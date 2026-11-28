import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import RetrieveSpan from "../RetrieveSpan";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, sx, ...props }) => {
    void sx;
    return <span data-testid="iconify" data-icon={icon} {...props} />;
  },
}));

vi.mock("src/sections/common/CellMarkdown", () => ({
  default: ({ text }) => <span>{text}</span>,
}));

const renderDocuments = (retreiveDocs) =>
  render(
    <RetrieveSpan
      column={{ headerName: "Documents", dataType: "text" }}
      value={{ cellValue: "What is RAG?" }}
      allowCopy
      showScore
      retreiveDocs={retreiveDocs}
    />,
  );

describe("RetrieveSpan", () => {
  it("shows the fallback value when no documents were retrieved", () => {
    renderDocuments({});

    expect(screen.getByText(/What is RAG\?/)).toBeInTheDocument();
    expect(screen.queryByText(/^Document /)).not.toBeInTheDocument();
  });

  it("shows the only retrieved document instead of the fallback value", () => {
    renderDocuments({
      doc1: { id: "doc1", value: "Retrieved document content", score: 0.83 },
    });

    expect(screen.getByText("Documents (1)")).toBeInTheDocument();
    expect(screen.getByText("Document doc1")).toBeInTheDocument();
    expect(screen.getByText("Score - 0.83")).toBeInTheDocument();
    expect(screen.getByText(/Retrieved document content/)).toBeInTheDocument();
    expect(screen.queryByText(/What is RAG\?/)).not.toBeInTheDocument();
  });

  it("shows every retrieved document when there are several", () => {
    renderDocuments({
      doc1: { id: "doc1", value: "First document", score: 0.9 },
      doc2: { id: "doc2", value: "Second document", score: 0.4 },
    });

    expect(screen.getByText("Documents (2)")).toBeInTheDocument();
    expect(screen.getByText("Document doc1")).toBeInTheDocument();
    expect(screen.getByText("Document doc2")).toBeInTheDocument();
    expect(screen.getByText(/First document/)).toBeInTheDocument();
    expect(screen.getByText(/Second document/)).toBeInTheDocument();
    expect(screen.queryByText(/What is RAG\?/)).not.toBeInTheDocument();
  });
});
