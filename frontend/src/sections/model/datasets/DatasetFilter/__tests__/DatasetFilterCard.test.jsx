import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import DatasetFilterCard from "../DatasetFilterCard";

describe("DatasetFilterCard", () => {
  it("calls addFilter when the add-filter button is clicked", async () => {
    const addFilter = vi.fn();
    render(<DatasetFilterCard addFilter={addFilter} />);

    await userEvent.click(screen.getByRole("button"));

    expect(addFilter).toHaveBeenCalledTimes(1);
  });
});
