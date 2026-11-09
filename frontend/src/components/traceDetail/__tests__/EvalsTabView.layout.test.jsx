import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import EvalsTabView from "../EvalsTabView";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

const evals = [
  {
    id: "row-1",
    eval_name: "Knowledge gap handling",
    score: null,
    score_items: ["gap_acknowledged"],
    spanName: "support_agent.run",
    spanId: "span-1",
  },
];

// jsdom has no layout: make every element report the given widths.
const mockLayout = ({ scrollWidth, clientWidth, rectWidth }) => {
  const restore = [
    vi
      .spyOn(HTMLElement.prototype, "scrollWidth", "get")
      .mockReturnValue(scrollWidth),
    vi
      .spyOn(HTMLElement.prototype, "clientWidth", "get")
      .mockReturnValue(clientWidth),
    vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({ width: rectWidth, height: 20, top: 0, left: 0 }),
  ];
  return () => restore.forEach((spy) => spy.mockRestore());
};

const memoryStorage = () => {
  const items = new Map();
  return {
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => items.set(key, String(value)),
    removeItem: (key) => items.delete(key),
    clear: () => items.clear(),
  };
};

describe("EvalsTabView layout", () => {
  let restoreLayout = () => {};

  beforeEach(() => {
    vi.stubGlobal("localStorage", memoryStorage());
  });

  afterEach(() => {
    restoreLayout();
    restoreLayout = () => {};
    vi.unstubAllGlobals();
  });

  it("shows the full eval name, choice and span on hover when truncated", async () => {
    restoreLayout = mockLayout({
      scrollWidth: 200,
      clientWidth: 80,
      rectWidth: 100,
    });
    render(<EvalsTabView evals={evals} onSelectSpan={() => {}} />);

    for (const text of [
      "Knowledge gap handling",
      "gap_acknowledged",
      "support_agent.run",
    ]) {
      const target = screen.getByText(text);
      fireEvent.mouseEnter(target);
      expect(await screen.findByRole("tooltip")).toHaveTextContent(text);
      fireEvent.mouseLeave(target);
      await waitFor(() =>
        expect(screen.queryByRole("tooltip")).not.toBeInTheDocument(),
      );
    }
  });

  it("shows no tooltip when the text fits", () => {
    restoreLayout = mockLayout({
      scrollWidth: 80,
      clientWidth: 80,
      rectWidth: 100,
    });
    render(<EvalsTabView evals={evals} onSelectSpan={() => {}} />);

    fireEvent.mouseEnter(screen.getByText("Knowledge gap handling"));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("resizes a column by dragging its header edge, and double-click resets", () => {
    restoreLayout = mockLayout({
      scrollWidth: 80,
      clientWidth: 80,
      rectWidth: 100,
    });
    render(<EvalsTabView evals={evals} onSelectSpan={() => {}} />);

    const handle = screen.getByTestId("evals-col-resize-name");
    fireEvent.mouseDown(handle, { clientX: 100 });
    fireEvent.mouseMove(document, { clientX: 160 });
    fireEvent.mouseUp(document);

    expect(screen.getByTestId("evals-col-header-name")).toHaveStyle({
      width: "160px",
    });
    expect(screen.getByTestId("evals-col-name-row-1")).toHaveStyle({
      width: "160px",
    });
    expect(
      JSON.parse(localStorage.getItem("evals-tab-col-widths:span")),
    ).toMatchObject({ name: 160, score: 100, span: 100 });

    fireEvent.doubleClick(handle);
    expect(screen.getByTestId("evals-col-header-name")).toHaveStyle({
      width: "30%",
    });
    expect(localStorage.getItem("evals-tab-col-widths:span")).toBeNull();
  });

  it("keeps a column at its minimum width", () => {
    restoreLayout = mockLayout({
      scrollWidth: 80,
      clientWidth: 80,
      rectWidth: 100,
    });
    render(<EvalsTabView evals={evals} onSelectSpan={() => {}} />);

    const handle = screen.getByTestId("evals-col-resize-score");
    fireEvent.mouseDown(handle, { clientX: 100 });
    fireEvent.mouseMove(document, { clientX: -500 });
    fireEvent.mouseUp(document);

    expect(screen.getByTestId("evals-col-header-score")).toHaveStyle({
      width: "60px",
    });
  });
});
