import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import FormSelectMenus from "./FormSelectMenus";

const defaultProps = {
  id: "test-menu",
  inputRef: { current: document.createElement("div") },
  open: true,
  onClose: vi.fn(),
  value: "",
  options: [
    { label: "Option 1", value: "opt1" },
    { label: "Option 2", value: "opt2" },
  ],
  onChange: vi.fn(),
  searchedValue: "",
  setSearchedValue: vi.fn(),
  menuPosition: "bottom",
};

describe("FormSelectMenus", () => {
  it("renders options when open", () => {
    render(<FormSelectMenus {...defaultProps} />);
    expect(screen.getByText("Option 1")).toBeInTheDocument();
    expect(screen.getByText("Option 2")).toBeInTheDocument();
  });

  it("does not use an excessively high z-index that would overlay dialogs", () => {
    const { baseElement } = render(<FormSelectMenus {...defaultProps} />);
    const popover = baseElement.querySelector(".MuiPopover-root");
    expect(popover).toBeTruthy();

    const style = window.getComputedStyle(popover);
    const zIndex = parseInt(style.zIndex, 10);
    // MUI Dialog uses z-index 1300. The dropdown must not exceed it,
    // otherwise it renders above modal dialogs and their backdrops.
    expect(zIndex).toBeLessThanOrEqual(1300);
  });

  it("offers an explicitly enabled typed value without pretending it was discovered", () => {
    const onChange = vi.fn();
    render(
      <FormSelectMenus
        {...defaultProps}
        searchedValue="custom.status"
        allowCreateFromSearch
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByText('Use "custom.status"'));

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0].target.value).toBe("custom.status");
  });
});
