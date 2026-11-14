import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import ScenarioToolbar from "../ScenarioToolbar";

const props = {
  query: "",
  onQueryChange: vi.fn(),
  view: "table",
  onViewChange: vi.fn(),
  // The page always hands the toolbar its filter fields; the filter panel reads them.
  filterFields: [],
  filters: [],
  onApplyFilters: vi.fn(),
};

describe("ScenarioToolbar — proof counts", () => {
  it("shows proved, kept as written and not proved apart", () => {
    render(
      <ScenarioToolbar
        {...props}
        proofCounts={{ proved: 1, kept_as_written: 1, not_proved: 1 }}
      />,
    );
    expect(screen.getByTestId("scenario-proof-counts")).toHaveTextContent(
      "1 proved · 1 kept as written · 1 not proved",
    );
  });

  it("shows no counts line when the server sends none", () => {
    render(<ScenarioToolbar {...props} />);
    expect(
      screen.queryByTestId("scenario-proof-counts"),
    ).not.toBeInTheDocument();
  });
});

describe("ScenarioToolbar — lifecycle filter", () => {
  it("sends the picked lifecycle", async () => {
    const user = userEvent.setup();
    const onLifecycleFilterChange = vi.fn();
    render(
      <ScenarioToolbar
        {...props}
        lifecycleFilter=""
        onLifecycleFilterChange={onLifecycleFilterChange}
      />,
    );
    await user.click(screen.getByRole("combobox", { name: "Lifecycle" }));
    await user.click(screen.getByRole("option", { name: "Source changed" }));
    expect(onLifecycleFilterChange).toHaveBeenCalledWith("source_changed");
  });
});
