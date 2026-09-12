import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import ShowModelDropdown from "./ShowModelDropdown";

describe("ShowModelDropdown filter tabs (#2436)", () => {
  it("clicking a type tab filters via onModelTypeChange and does not select a model", async () => {
    const onModelTypeChange = vi.fn();
    const onChange = vi.fn();
    const anchor = document.createElement("div");
    document.body.appendChild(anchor);
    Object.defineProperty(anchor, "offsetWidth", { value: 400 });
    anchor.getBoundingClientRect = () => ({
      bottom: 40,
      top: 0,
      left: 0,
      right: 400,
      width: 400,
      height: 32,
    });

    render(
      <ShowModelDropdown
        ref={{ current: anchor }}
        open
        onClose={vi.fn()}
        options={[
          {
            modelName: "gpt-4o",
            providers: "openai",
            isAvailable: true,
            logoUrl: "",
            type: "chat",
          },
        ]}
        value="gpt-4o"
        onChange={onChange}
        onModelTypeChange={onModelTypeChange}
        modelType="all"
        setSearchQuery={vi.fn()}
        fetchNextPage={vi.fn()}
      />,
    );

    const user = userEvent.setup();
    await user.click(screen.getByText("Image"));

    expect(onModelTypeChange).toHaveBeenCalledWith("image");
    expect(onChange).not.toHaveBeenCalled();
  });
});
