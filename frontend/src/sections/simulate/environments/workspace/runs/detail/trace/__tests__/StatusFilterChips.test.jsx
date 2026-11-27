import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import StatusFilterChips from "../StatusFilterChips";

const chips = (props) => (
  <StatusFilterChips value="all" onChange={vi.fn()} {...props} />
);

describe("StatusFilterChips", () => {
  it("shows every status greyed before the first counts arrive", () => {
    render(chips({ counts: {}, firstLoad: true }));
    expect(screen.getByRole("button", { name: "Queued" })).toBeInTheDocument();
  });

  it("keeps the bar as it was while the table reloads with the previous counts", () => {
    const { rerender } = render(
      chips({ counts: { all: 9, passed: 2, failed: 7 } }),
    );
    expect(screen.queryByRole("button", { name: "Queued" })).toBeNull();

    // Picking a status reloads the table; keepPreviousData hands back the
    // previous counts, so it is not a first load.
    rerender(
      chips({
        value: "failed",
        counts: { all: 9, passed: 2, failed: 7 },
      }),
    );
    expect(screen.queryByRole("button", { name: "Queued" })).toBeNull();
    expect(screen.getByRole("button", { name: "Failed" })).toHaveTextContent(
      "7",
    );
  });

  it("keeps the bar to All after an empty result when the table reloads", () => {
    // A filter that matched nothing leaves all-zero counts. The next reload
    // is not a first load, so the bar must not grow to every status.
    render(chips({ counts: { all: 0 }, firstLoad: false }));
    expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Queued" })).toBeNull();
  });

  it("shows a status segment when its count goes from 0 to more", () => {
    const { rerender } = render(chips({ counts: { all: 3, passed: 3 } }));
    expect(screen.queryByRole("button", { name: "Failed" })).toBeNull();

    rerender(chips({ counts: { all: 5, passed: 3, failed: 2 } }));
    expect(screen.getByRole("button", { name: "Failed" })).toHaveTextContent(
      "2",
    );
  });
});
