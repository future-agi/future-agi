import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LocalDateTime } from "./LocalDateTime";

const NativeDateTimeFormat = Intl.DateTimeFormat;
const INSTANT = "2025-10-31T00:00:00Z";
// A closing tooltip stays in the DOM until its fade-out ends.
const outlastCloseTransition = () =>
  act(() => new Promise((resolve) => setTimeout(resolve, 400)));

describe("LocalDateTime", () => {
  beforeEach(() => {
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation((locale, options) =>
      locale === undefined
        ? { resolvedOptions: () => ({ timeZone: "Asia/Kolkata" }) }
        : new NativeDateTimeFormat(locale, options),
    );
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders a focusable date with instant details available on keyboard focus", async () => {
    const user = userEvent.setup();
    render(<LocalDateTime value={INSTANT} />);
    const date = screen.getByText("31 Oct 2025");
    expect(date.tagName).toBe("SPAN");
    expect(date).toHaveAttribute("tabindex", "0");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    await user.tab();
    expect(date).toHaveFocus();
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Local: 31 Oct 2025, 5:30 AM");
    expect(tooltip).toHaveTextContent("Zone: Asia/Kolkata (UTC+05:30)");
    expect(tooltip).toHaveTextContent("UTC: 2025-10-31T00:00:00.000Z");
    expect(date).toHaveAttribute("aria-describedby", tooltip.id);
  });

  it("includes time in the inline text when requested", () => {
    render(<LocalDateTime value={INSTANT} withTime />);
    expect(screen.getByText("31 Oct 2025, 5:30 AM")).toBeInTheDocument();
  });

  it.each([null, "garbage", "2025-10-31", "2025-10-31T05:30:00"])(
    "renders emptyText without a focus target or tooltip for %s",
    (value) => {
      const { container } = render(
        <LocalDateTime value={value} emptyText="Unknown" />,
      );
      expect(screen.getByText("Unknown")).toBeInTheDocument();
      expect(container.querySelector("[tabindex]")).toBeNull();
      fireEvent.focus(screen.getByText("Unknown"));
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    },
  );

  it("shows Unknown by default and no tooltip for an invalid value", () => {
    const { container } = render(
      <LocalDateTime value="<script>garbage</script>" />,
    );
    expect(screen.getByText("Unknown")).toBeInTheDocument();
    expect(container.querySelector("[tabindex]")).toBeNull();
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  // A tap is touchstart, touchend, then the click the browser fires for it.
  it("keeps the disclosure open after a full tap", async () => {
    render(<LocalDateTime value={INSTANT} />);
    const date = screen.getByText("31 Oct 2025");
    fireEvent.touchStart(date);
    fireEvent.touchEnd(date);
    fireEvent.click(date);
    await outlastCloseTransition();
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveTextContent("Zone: Asia/Kolkata (UTC+05:30)");
    expect(tooltip).toHaveTextContent("UTC: 2025-10-31T00:00:00.000Z");
  });

  it("keeps the disclosure open when the hovered date is clicked", async () => {
    const user = userEvent.setup();
    render(<LocalDateTime value={INSTANT} />);
    const date = screen.getByText("31 Oct 2025");
    await user.hover(date);
    await screen.findByRole("tooltip");
    await user.click(date);
    await outlastCloseTransition();
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
  });
});
