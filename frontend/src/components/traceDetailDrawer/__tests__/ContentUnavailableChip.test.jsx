import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ContentUnavailableChip from "../ContentUnavailableChip";

describe("ContentUnavailableChip", () => {
  it("unit: shows the chip when a side's full content is unavailable", () => {
    render(<ContentUnavailableChip sides={["input"]} />);
    expect(screen.getByTestId("content-unavailable-chip")).toBeInTheDocument();
    expect(
      screen.getByText("Full content unavailable — preview shown"),
    ).toBeInTheDocument();
  });

  it("unit: renders nothing for spans with full content", () => {
    const { container } = render(<ContentUnavailableChip sides={[]} />);
    expect(container).toBeEmptyDOMElement();
    render(<ContentUnavailableChip />);
    expect(screen.queryByTestId("content-unavailable-chip")).toBeNull();
  });

  it("unit: ignores unknown side names", () => {
    render(<ContentUnavailableChip sides={["metadata"]} />);
    expect(screen.queryByTestId("content-unavailable-chip")).toBeNull();
  });
});
