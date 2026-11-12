import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import CustomAnalyticsRangeDialog from "./CustomAnalyticsRangeDialog";

const start = "2026-11-01T09:12:34.123Z";
const end = "2026-11-01T09:28:56.456Z";
const change = (label, value) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

describe("Custom analytics interval", () => {
  it("preserves exact original instants when unchanged", () => {
    const onApply = vi.fn();
    render(
      <CustomAnalyticsRangeDialog
        start={start}
        end={end}
        onApply={onApply}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/Times are in/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Apply interval" }));
    expect(onApply).toHaveBeenCalledWith({ start, end });
  });

  it("converts edited local inputs into offset-bearing instants", () => {
    const onApply = vi.fn();
    render(
      <CustomAnalyticsRangeDialog
        start={start}
        end={end}
        onApply={onApply}
        onClose={vi.fn()}
      />,
    );
    change("From", "2026-11-02T09:12");
    change("To", "2026-11-02T09:28");
    fireEvent.click(screen.getByRole("button", { name: "Apply interval" }));
    expect(onApply).toHaveBeenCalledWith({
      start: new Date("2026-11-02T09:12").toISOString(),
      end: new Date("2026-11-02T09:28").toISOString(),
    });
  });

  it.each([
    ["", "2026-11-02T09:28"],
    ["2026-11-02T09:28", "2026-11-02T09:12"],
    ["2026-11-02T09:12", "2026-11-02T09:12"],
    ["2020-01-01T00:00", "2026-11-02T09:12"],
  ])("rejects invalid or overlarge interval %s to %s", (from, to) => {
    const onApply = vi.fn();
    render(
      <CustomAnalyticsRangeDialog
        start={start}
        end={end}
        onApply={onApply}
        onClose={vi.fn()}
      />,
    );
    change("From", from);
    change("To", to);
    expect(
      screen.getByRole("button", { name: "Apply interval" }),
    ).toBeDisabled();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("cancels without applying a draft", () => {
    const onApply = vi.fn();
    const onClose = vi.fn();
    render(
      <CustomAnalyticsRangeDialog
        start={start}
        end={end}
        onApply={onApply}
        onClose={onClose}
      />,
    );
    change("From", "2026-11-02T09:12");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(onApply).not.toHaveBeenCalled();
  });
});
