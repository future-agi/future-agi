import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ImagesDatapointCard from "./ImagesDatapointCard";

const column = { headerName: "Screenshots" };

describe("ImagesDatapointCard", () => {
  it("renders the column header name", () => {
    render(<ImagesDatapointCard value={{ cell_value: [] }} column={column} />);
    expect(screen.getByText("Screenshots")).toBeInTheDocument();
  });

  it("renders one image per url from a JSON-string cell_value", () => {
    const urls = ["https://cdn.test/a.png", "https://cdn.test/b.png"];
    render(
      <ImagesDatapointCard
        value={{ cell_value: JSON.stringify(urls) }}
        column={column}
      />,
    );
    expect(screen.getByAltText("Image 1")).toHaveAttribute("src", urls[0]);
    expect(screen.getByAltText("Image 2")).toHaveAttribute("src", urls[1]);
  });

  it("renders images when cell_value is already an array", () => {
    const urls = ["https://cdn.test/only.png"];
    render(
      <ImagesDatapointCard value={{ cell_value: urls }} column={column} />,
    );
    expect(screen.getByAltText("Image 1")).toHaveAttribute("src", urls[0]);
  });

  it("shows the placeholder when cell_value is missing", () => {
    render(<ImagesDatapointCard value={{}} column={column} />);
    expect(screen.getByAltText("No images placeholder")).toBeInTheDocument();
    expect(screen.queryByAltText("Image 1")).not.toBeInTheDocument();
  });

  it("does not read the camelCase cellValue key", () => {
    render(
      <ImagesDatapointCard
        value={{ cellValue: ["https://cdn.test/ignored.png"] }}
        column={column}
      />,
    );
    expect(screen.getByAltText("No images placeholder")).toBeInTheDocument();
    expect(screen.queryByAltText("Image 1")).not.toBeInTheDocument();
  });
});
