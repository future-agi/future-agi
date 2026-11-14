import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import DocumentDatapointCard from "./DocumentDatapointCard";

const column = { headerName: "Attachment" };

describe("DocumentDatapointCard", () => {
  it("renders the column header name", () => {
    render(<DocumentDatapointCard value={{}} column={column} />);
    expect(screen.getByText("Attachment")).toBeInTheDocument();
  });

  it("renders the file name and type from cell_value", () => {
    render(
      <DocumentDatapointCard
        value={{ cell_value: "https://cdn.test/docs/report.pdf" }}
        column={column}
      />,
    );
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
    expect(screen.getByText("PDF")).toBeInTheDocument();
  });

  it("shows the empty state when cell_value is missing", () => {
    render(<DocumentDatapointCard value={{}} column={column} />);
    expect(screen.getByText("No document added")).toBeInTheDocument();
  });

  it("does not read the camelCase cellValue key", () => {
    render(
      <DocumentDatapointCard
        value={{ cellValue: "https://cdn.test/docs/ignored.pdf" }}
        column={column}
      />,
    );
    expect(screen.getByText("No document added")).toBeInTheDocument();
    expect(screen.queryByText("ignored.pdf")).not.toBeInTheDocument();
  });
});
