import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CustomDateRangePicker from "../DatePicker";

const noop = () => {};

function renderPicker(props) {
  return render(
    <CustomDateRangePicker
      open={false}
      onClose={noop}
      anchorEl={document.body}
      setDateFilter={vi.fn()}
      setDateOption={vi.fn()}
      {...props}
    />,
  );
}

const MARCH = [new Date("2026-03-10T12:00:00Z"), new Date("2026-04-10T12:00:00Z")];
const AUGUST = [new Date("2026-08-10T12:00:00Z"), new Date("2026-09-10T12:00:00Z")];

describe("CustomDateRangePicker seeding effect", () => {
  it("seeds the calendars from value on the open false→true transition", () => {
    const { rerender } = renderPicker({ value: MARCH });
    rerender(
      <CustomDateRangePicker
        open
        onClose={noop}
        anchorEl={document.body}
        setDateFilter={vi.fn()}
        setDateOption={vi.fn()}
        value={MARCH}
      />,
    );
    expect(screen.getByText("March 2026")).toBeInTheDocument();
    expect(screen.getByText("April 2026")).toBeInTheDocument();
  });

  it("does NOT reseed when value changes while already open", () => {
    const { rerender } = renderPicker({ value: MARCH });
    // open false→true: seeds March
    rerender(
      <CustomDateRangePicker
        open
        onClose={noop}
        anchorEl={document.body}
        setDateFilter={vi.fn()}
        setDateOption={vi.fn()}
        value={MARCH}
      />,
    );
    expect(screen.getByText("March 2026")).toBeInTheDocument();

    // value changes while still open → must keep March, not jump to August
    rerender(
      <CustomDateRangePicker
        open
        onClose={noop}
        anchorEl={document.body}
        setDateFilter={vi.fn()}
        setDateOption={vi.fn()}
        value={AUGUST}
      />,
    );
    expect(screen.getByText("March 2026")).toBeInTheDocument();
    expect(screen.queryByText("August 2026")).not.toBeInTheDocument();
  });

  it("ignores a malformed value (no seed, Done stays disabled, no crash)", () => {
    const bad = [new Date("nope"), new Date("nope")];
    const { rerender } = renderPicker({ value: bad });
    rerender(
      <CustomDateRangePicker
        open
        onClose={noop}
        anchorEl={document.body}
        setDateFilter={vi.fn()}
        setDateOption={vi.fn()}
        value={bad}
      />,
    );
    expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
  });
});
