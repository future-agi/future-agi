import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import TagPicker from "../TagPicker";

const typeNewTag = async (text) => {
  await userEvent.click(screen.getByRole("button", { name: "Edit tags" }));
  await userEvent.click(
    screen.getByPlaceholderText("Type new tag and press Enter"),
  );
  await userEvent.paste(text);
  await userEvent.keyboard("{Enter}");
};

describe("TagPicker", () => {
  it("refuses a new tag over maxTagLength and says why", async () => {
    const onChange = vi.fn();
    render(<TagPicker tags={[]} onChange={onChange} maxTagLength={4} />);
    await typeNewTag("abcde");
    expect(
      screen.getByText("A tag can be at most 4 characters"),
    ).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("adds a tag at the limit", async () => {
    const onChange = vi.fn();
    render(<TagPicker tags={[]} onChange={onChange} maxTagLength={4} />);
    await typeNewTag("abcd");
    expect(onChange).toHaveBeenCalledWith(["abcd"]);
  });

  it("puts no limit on a picker that does not ask for one", async () => {
    const onChange = vi.fn();
    render(<TagPicker tags={[]} onChange={onChange} />);
    await typeNewTag("x".repeat(100));
    expect(onChange).toHaveBeenCalledWith(["x".repeat(100)]);
  });
});
