import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import SaveViewPopover from "../SaveViewDialog";

// The popover renders inside a MUI Popover portal; anchorEl just needs to be
// a truthy element. Tests assert the inline duplicate-name guard.
describe("SaveViewPopover — duplicate-name guard", () => {
  let onSave;
  let onClose;

  const renderPopover = (props = {}) => {
    const anchor = document.createElement("button");
    document.body.appendChild(anchor);
    return render(
      <SaveViewPopover
        anchorEl={anchor}
        open
        onClose={onClose}
        onSave={onSave}
        {...props}
      />,
    );
  };

  beforeEach(() => {
    onSave = vi.fn();
    onClose = vi.fn();
  });

  it("saves a unique name", () => {
    renderPopover({ existingNames: ["Alpha"] });
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Beta" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save view" }));
    expect(onSave).toHaveBeenCalledWith("Beta");
  });

  it("blocks save and shows an error for an exact duplicate name (after trim)", () => {
    renderPopover({ existingNames: ["My View"] });
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "  My View  " },
    });
    expect(
      screen.getByText("A view with this name already exists."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save view" }));
    expect(onSave).not.toHaveBeenCalled();
  });

  it("allows a case-variant name (backend uniqueness is case-sensitive)", () => {
    renderPopover({ existingNames: ["My View"] });
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "my view" },
    });
    expect(
      screen.queryByText("A view with this name already exists."),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save view" }));
    expect(onSave).toHaveBeenCalledWith("my view");
  });

  it("saves a unique name on Enter", () => {
    renderPopover({ existingNames: ["Dup"] });
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Fresh" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSave).toHaveBeenCalledWith("Fresh");
  });

  it("blocks save on Enter for a duplicate name", () => {
    renderPopover({ existingNames: ["Dup"] });
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Dup" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSave).not.toHaveBeenCalled();
  });

  it("does not treat an empty existingNames list as a conflict", () => {
    renderPopover();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Anything" },
    });
    expect(
      screen.queryByText("A view with this name already exists."),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save view" }));
    expect(onSave).toHaveBeenCalledWith("Anything");
  });
});
