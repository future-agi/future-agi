import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LocalDateTime } from "./LocalDateTime";

const NativeDateTimeFormat = Intl.DateTimeFormat;
const INSTANT = "2025-10-31T00:00:00Z";

describe("LocalDateTime", () => {
  beforeEach(() => {
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation(
      (locale, options) =>
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

  it("leaves invalid grid values empty by default", () => {
    const { container } = render(
      <LocalDateTime value="<script>garbage</script>" />,
    );
    expect(container.textContent).toBe("");
    expect(container.querySelector("[tabindex]")).toBeNull();
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
