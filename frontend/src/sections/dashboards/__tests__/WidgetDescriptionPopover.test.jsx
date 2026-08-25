import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import WidgetDescriptionPopover from "../WidgetDescriptionPopover";

const setup = (props = {}) => {
  const onChange = vi.fn();
  const onClose = vi.fn();
  const anchor = document.createElement("div");
  document.body.appendChild(anchor);
  render(
    <WidgetDescriptionPopover
      open
      anchorEl={anchor}
      value=""
      onChange={onChange}
      onClose={onClose}
      {...props}
    />,
  );
  return { onChange, onClose };
};

describe("WidgetDescriptionPopover", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the current description", () => {
    setup({ value: "p95 latency across production calls" });
    expect(
      screen.getByDisplayValue("p95 latency across production calls"),
    ).toBeInTheDocument();
  });

  it("reports edits as a plain string, not an event", () => {
    const { onChange } = setup();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Error rate by model" },
    });
    expect(onChange).toHaveBeenCalledWith("Error rate by model");
  });

  it("closes on Done", () => {
    const { onClose } = setup({ value: "Anything" });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on modifier+Enter but not on a bare Enter, which inserts a line break", () => {
    const { onClose } = setup({ value: "Line one" });
    const field = screen.getByRole("textbox");

    fireEvent.keyDown(field, { key: "Enter" });
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.keyDown(field, { key: "Enter", metaKey: true });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders nothing while closed", () => {
    setup({ open: false, value: "Hidden" });
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});
