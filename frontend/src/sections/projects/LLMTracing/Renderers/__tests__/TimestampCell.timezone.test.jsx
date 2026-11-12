import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import { getDisplayTimeZone } from "src/utils/format-time";
import TimestampCell from "../TimestampCell";

// The viewer's zone comes from format-time. Pin it per test so the result
// never depends on the zone of the machine that runs the suite.
vi.mock("src/utils/format-time", async (importOriginal) => ({
  ...(await importOriginal()),
  getDisplayTimeZone: vi.fn(() => "UTC"),
}));

// A trace that started at 05:49:38 UTC, viewed three minutes later.
const STARTED = "2026-11-11T05:49:38.000Z";
const NOW = new Date("2026-11-11T05:52:38.000Z");
const UTC_TEXT = "Nov 11, 2026 05:49:38 UTC";
// Finds the cell by its date and not its zone name, so each test below
// reaches its own assertion. A cell that formats in the runner's zone shows
// the day before west of UTC-05:49, so that day matches too.
const CELL_DATE = /^Nov 1[01], 2026/;

describe("TimestampCell timezone and relative time", () => {
  beforeEach(() => {
    // Only Date is faked: the tooltip's enter delay keeps real timers.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    vi.mocked(getDisplayTimeZone).mockReturnValue("UTC");
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it.each([
    ["UTC", UTC_TEXT],
    ["Asia/Kolkata", "Nov 11, 2026 11:19:38 GMT+5:30"],
    ["America/New_York", "Nov 11, 2026 00:49:38 EST"],
  ])(
    "shows the time in the viewer's zone with its short name (%s)",
    (zone, text) => {
      vi.mocked(getDisplayTimeZone).mockReturnValue(zone);
      render(<TimestampCell value={STARTED} />);
      expect(screen.getByText(text)).toBeInTheDocument();
    },
  );

  it("marks the value up as a time element that carries the ISO instant", () => {
    render(<TimestampCell value={STARTED} />);
    const time = screen.getByText(CELL_DATE);
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("datetime", "2026-11-11T05:49:38.000Z");
  });

  it("shows the relative time and the full ISO timestamp on hover", async () => {
    const user = userEvent.setup();
    render(<TimestampCell value={STARTED} />);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    await user.hover(screen.getByText(CELL_DATE));

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("3 minutes ago");
    expect(tooltip).toHaveTextContent("2026-11-11T05:49:38.000Z");
  });

  it("opens the same tooltip on keyboard focus and describes the time with it", async () => {
    const user = userEvent.setup();
    render(<TimestampCell value={STARTED} />);
    const time = screen.getByText(CELL_DATE);

    await user.tab();

    expect(time).toHaveFocus();
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("3 minutes ago");
    expect(tooltip).toHaveTextContent("2026-11-11T05:49:38.000Z");
    expect(time).toHaveAttribute("aria-describedby", tooltip.id);
    // The tooltip describes the time; it does not replace its name.
    expect(time).not.toHaveAttribute("aria-label");
  });

  it.each([null, "not-a-date"])(
    "keeps '-' with no focus stop or tooltip for %s",
    (value) => {
      const { container } = render(<TimestampCell value={value} />);
      expect(screen.getByText("-")).toBeInTheDocument();
      expect(container.querySelector("time")).toBeNull();
      expect(container.querySelector("[tabindex]")).toBeNull();
    },
  );
});
