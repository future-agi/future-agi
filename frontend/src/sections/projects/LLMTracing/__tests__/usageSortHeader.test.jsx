import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import CustomTraceHeaderRenderer from "../Renderers/CustomTraceHeaderRenderer";

vi.mock("src/components/iconify", () => ({ default: () => null }));

describe("usage sort header", () => {
  it("leaves nonsortable columns without an interactive sort affordance", () => {
    render(
      <CustomTraceHeaderRenderer displayName="Input" enableSorting={false} />,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("Input")).toBeInTheDocument();
  });

  it("delegates the accessible button to AG Grid's real sort transport", () => {
    const progressSort = vi.fn();
    const column = {
      getSort: () => "asc",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    const { unmount } = render(
      <CustomTraceHeaderRenderer
        displayName="Tokens"
        column={column}
        enableSorting
        progressSort={progressSort}
      />,
    );
    const button = screen.getByRole("button", { name: "Sort Tokens" });
    expect(button).toHaveAttribute(
      "title",
      "Sorted ascending; click to change",
    );
    fireEvent.click(button);
    expect(progressSort).toHaveBeenCalledWith(false);
    fireEvent.click(button, { shiftKey: true });
    expect(progressSort).toHaveBeenCalledWith(true);
    const listener = column.addEventListener.mock.calls[0][1];
    unmount();
    expect(column.removeEventListener).toHaveBeenCalledWith(
      "sortChanged",
      listener,
    );
  });
});
