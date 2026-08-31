import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "src/utils/test-utils";
import ThinkingStrip from "../ThinkingStrip";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("ThinkingStrip", () => {
  it("says what the harness is doing", () => {
    render(<ThinkingStrip label="thinking" />);
    expect(screen.getByText("thinking")).toBeInTheDocument();
  });

  it("starts the clock at zero", () => {
    render(<ThinkingStrip label="thinking" />);
    expect(screen.getByText("0s")).toBeInTheDocument();
  });

  it("counts the wait so a long stage does not look like a hang", () => {
    render(<ThinkingStrip label="thinking" />);
    act(() => vi.advanceTimersByTime(4000));
    expect(screen.getByText("4s")).toBeInTheDocument();
  });

  it("shows three dots", () => {
    const { container } = render(<ThinkingStrip label="thinking" />);
    expect(container.querySelectorAll("i")).toHaveLength(3);
  });
});
