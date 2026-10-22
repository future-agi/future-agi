import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, userEvent } from "src/utils/test-utils";
import CustomTagInput from "../CustomTagInput";
import { withPendingTag } from "../tagUtils";

const setup = (tags = []) => {
  const onAdd = vi.fn();
  const onChange = vi.fn();
  const view = render(
    <CustomTagInput value="" onChange={onChange} onAdd={onAdd} />,
  );
  return { onAdd, onChange, view, tags };
};

describe("Unit: CustomTagInput", () => {
  it("commits the trimmed value on Enter and clears the box", async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    const onChange = vi.fn();
    const { rerender } = render(
      <CustomTagInput
        value="  client-demo  "
        onChange={onChange}
        onAdd={onAdd}
      />,
    );

    await user.type(
      screen.getByRole("textbox", { name: "Add custom tag" }),
      "{Enter}",
    );

    expect(onAdd).toHaveBeenCalledWith("client-demo");
    expect(onChange).toHaveBeenLastCalledWith("");
    rerender(<CustomTagInput value="" onChange={onChange} onAdd={onAdd} />);
  });

  it("ignores an Enter that only confirms an IME composition", () => {
    const { onAdd } = setup();
    const input = screen.getByRole("textbox", { name: "Add custom tag" });

    fireEvent.keyDown(input, {
      key: "Enter",
      nativeEvent: { isComposing: true },
      isComposing: true,
    });

    expect(onAdd).not.toHaveBeenCalled();
  });

  it("does not commit a whitespace-only value", async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    render(<CustomTagInput value="   " onChange={vi.fn()} onAdd={onAdd} />);

    await user.type(
      screen.getByRole("textbox", { name: "Add custom tag" }),
      "{Enter}",
    );

    expect(onAdd).not.toHaveBeenCalled();
  });
});

describe("Unit: withPendingTag", () => {
  it("folds an uncommitted tag into the saved list", () => {
    expect(withPendingTag(["safety"], "  client-demo ")).toEqual([
      "safety",
      "client-demo",
    ]);
  });

  it("leaves the list alone when the box is empty or whitespace", () => {
    const tags = ["safety"];
    expect(withPendingTag(tags, "")).toBe(tags);
    expect(withPendingTag(tags, "   ")).toBe(tags);
    expect(withPendingTag(tags, undefined)).toBe(tags);
  });

  it("does not duplicate a tag that is already selected", () => {
    const tags = ["client-demo"];
    expect(withPendingTag(tags, "client-demo")).toBe(tags);
  });
});
