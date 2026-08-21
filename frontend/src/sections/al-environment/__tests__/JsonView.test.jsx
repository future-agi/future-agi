import { describe, it, expect } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import JsonView from "../JsonView";

describe("JsonView", () => {
  it("renders keys and string values", () => {
    render(<JsonView value={{ name: "drive_thru" }} />);
    expect(screen.getByText(/name/)).toBeInTheDocument();
    expect(screen.getByText(/drive_thru/)).toBeInTheDocument();
  });

  it("renders numbers and booleans", () => {
    render(<JsonView value={{ count: 3, ready: true }} />);
    expect(screen.getByText(/3/)).toBeInTheDocument();
    expect(screen.getByText(/true/)).toBeInTheDocument();
  });

  it("renders nested structures without throwing", () => {
    render(<JsonView value={{ tools: [{ name: "order" }, { name: "refund" }] }} />);
    expect(screen.getByText(/refund/)).toBeInTheDocument();
  });

  it("says so when there is nothing to show", () => {
    render(<JsonView value={null} />);
    expect(screen.getByText(/nothing/i)).toBeInTheDocument();
  });

  it("counts what each container holds", () => {
    render(<JsonView value={{ tools: ["a", "b"], agent: "drive_thru" }} />);
    expect(screen.getByText("{2}")).toBeInTheDocument();
    expect(screen.getByText("[2]")).toBeInTheDocument();
  });

  it("opens the top two levels and leaves the rest folded", () => {
    const { container } = render(
      <JsonView value={{ tools: [{ name: "order", args: ["item"] }] }} />
    );
    const folds = [...container.querySelectorAll("details")];
    // root {1} and tools [1] are open; the tool object at depth 2 is not.
    expect(folds.filter((node) => node.open)).toHaveLength(2);
    expect(folds.filter((node) => !node.open)).toHaveLength(2);
  });

  it("expands and collapses the whole tree on demand", async () => {
    const { container } = render(
      <JsonView value={{ tools: [{ name: "order", args: ["item"] }] }} />
    );
    const allFolds = () => [...container.querySelectorAll("details")];

    await userEvent.click(screen.getByRole("button", { name: "expand all" }));
    expect(allFolds().every((node) => node.open)).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "collapse all" }));
    expect(allFolds().every((node) => !node.open)).toBe(true);
  });

  it("shows strings as JSON, so escapes are visible", () => {
    render(<JsonView value={{ why: 'said "no"\nthen left' }} />);
    expect(screen.getByText('"said \\"no\\"\\nthen left"')).toBeInTheDocument();
  });

  it("shows null and empty containers rather than dropping them", () => {
    render(<JsonView value={{ why: null, files: [], gates: {} }} />);
    expect(screen.getByText("null")).toBeInTheDocument();
    expect(screen.getByText("[]")).toBeInTheDocument();
    expect(screen.getByText("{}")).toBeInTheDocument();
  });
});
