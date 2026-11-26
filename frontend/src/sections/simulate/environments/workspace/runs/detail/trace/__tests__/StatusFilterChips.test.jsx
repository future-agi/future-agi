import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import StatusFilterChips from "../StatusFilterChips";

const chips = (props) => (
  <StatusFilterChips value="all" onChange={vi.fn()} {...props} />
);

describe("StatusFilterChips", () => {
  it("shows every status greyed before the first counts arrive", () => {
    render(chips({ counts: {}, loading: true }));
    expect(screen.getByRole("button", { name: "Queued" })).toBeInTheDocument();
  });

  it("keeps the last counts while the table reloads, so the bar doesn't flash", () => {
    const { rerender } = render(chips({ counts: { passed: 2, failed: 7 } }));
    expect(screen.queryByRole("button", { name: "Queued" })).toBeNull();

    // Picking a status reloads the table; its counts are empty meanwhile.
    rerender(chips({ value: "failed", counts: {}, loading: true }));
    expect(screen.queryByRole("button", { name: "Queued" })).toBeNull();
    expect(screen.getByRole("button", { name: "Failed" })).toHaveTextContent(
      "7",
    );
  });
});
