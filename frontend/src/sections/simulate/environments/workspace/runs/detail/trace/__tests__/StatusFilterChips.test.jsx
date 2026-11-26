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

  it("keeps the bar as it was while the table reloads with the previous counts", () => {
    const { rerender } = render(
      chips({ counts: { all: 9, passed: 2, failed: 7 } }),
    );
    expect(screen.queryByRole("button", { name: "Queued" })).toBeNull();

    // Picking a status reloads the table; keepPreviousData hands back the
    // previous counts while loading is true.
    rerender(
      chips({
        value: "failed",
        counts: { all: 9, passed: 2, failed: 7 },
        loading: true,
      }),
    );
    expect(screen.queryByRole("button", { name: "Queued" })).toBeNull();
    expect(screen.getByRole("button", { name: "Failed" })).toHaveTextContent(
      "7",
    );
  });
});
