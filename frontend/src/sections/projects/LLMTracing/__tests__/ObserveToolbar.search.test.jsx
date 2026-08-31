import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../TraceFilterPanel", () => ({ default: () => null }));
vi.mock("../DisplayPanel", () => ({ default: () => null }));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));
vi.mock("../tabStore", () => ({
  useTabStoreShallow: (selector) => selector({ openCreateModal: vi.fn() }),
}));

import ObserveToolbar from "../ObserveToolbar";

const baseProps = {
  inline: true,
  mode: "traces",
  onSearchApply: vi.fn(),
};

describe("ObserveToolbar free-text search", () => {
  it("renders the search input in traces mode", () => {
    render(<ObserveToolbar {...baseProps} />);
    expect(screen.getByPlaceholderText(/search traces/i)).toBeInTheDocument();
  });

  it("applies the trimmed draft on Enter", async () => {
    const user = userEvent.setup();
    const onSearchApply = vi.fn();
    render(<ObserveToolbar {...baseProps} onSearchApply={onSearchApply} />);

    const input = screen.getByPlaceholderText(/search traces/i);
    await user.type(input, "  timeout  {Enter}");
    expect(onSearchApply).toHaveBeenCalledTimes(1);
    expect(onSearchApply).toHaveBeenCalledWith("timeout");
  });

  it("does not apply while typing without Enter", async () => {
    const user = userEvent.setup();
    const onSearchApply = vi.fn();
    render(<ObserveToolbar {...baseProps} onSearchApply={onSearchApply} />);

    await user.type(screen.getByPlaceholderText(/search traces/i), "timeout");
    expect(onSearchApply).not.toHaveBeenCalled();
  });

  it("syncs the draft when the applied term changes externally", () => {
    const { rerender } = render(
      <ObserveToolbar {...baseProps} searchTerm="first" />,
    );
    expect(screen.getByPlaceholderText(/search traces/i)).toHaveValue("first");

    rerender(<ObserveToolbar {...baseProps} searchTerm="" />);
    expect(screen.getByPlaceholderText(/search traces/i)).toHaveValue("");
  });

  it("hides the search input outside traces mode", () => {
    render(<ObserveToolbar {...baseProps} mode="sessions" />);
    expect(
      screen.queryByPlaceholderText(/search traces/i),
    ).not.toBeInTheDocument();
  });

  it("hides the search input in compare mode", () => {
    render(<ObserveToolbar {...baseProps} isCompareActive />);
    expect(
      screen.queryByPlaceholderText(/search traces/i),
    ).not.toBeInTheDocument();
  });

  it("hides the search input when no onSearchApply handler is wired", () => {
    render(<ObserveToolbar inline mode="traces" />);
    expect(
      screen.queryByPlaceholderText(/search traces/i),
    ).not.toBeInTheDocument();
  });
});
