import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

import EvalFilterPanel from "../EvalFilterPanel";

// The Evals filter panel has its own inline QueryInput (the "Query" tab), which
// is a separate implementation from the shared Observe `QueryInput`. Its AND/OR
// combinator must be exercised independently: two populated filters render one
// separator, toggling it re-applies through onApply(apiFilters, combinator).

vi.mock("src/hooks/use-ai-filter", () => ({
  useAIFilter: () => ({
    parseQuery: vi.fn(),
    loading: false,
    error: null,
  }),
}));

function renderPanel({
  currentFilters = {
    eval_type: ["llm"],
    output_type: ["pass_fail"],
  },
  onApply = vi.fn(),
  onClose = vi.fn(),
  open = true,
  showQueryCombinator,
  currentCombinator,
} = {}) {
  const anchorEl = document.createElement("button");
  document.body.appendChild(anchorEl);
  const panelProps = {
    anchorEl,
    open,
    onClose,
    onApply,
    currentFilters,
    lockedFilters: {},
  };
  if (showQueryCombinator !== undefined)
    panelProps.showQueryCombinator = showQueryCombinator;
  if (currentCombinator) panelProps.currentCombinator = currentCombinator;
  const utils = render(<EvalFilterPanel {...panelProps} />);
  return { anchorEl, onApply, onClose, ...utils };
}

describe("EvalFilterPanel Query tab AND/OR combinator (#2226)", () => {
  it("shows an AND separator between two populated filters", async () => {
    const user = userEvent.setup();
    const { anchorEl } = renderPanel();

    await user.click(screen.getByRole("tab", { name: "Query" }));

    const separator = screen.getByRole("button", {
      name: "Combine filters with AND",
    });
    expect(separator).toBeInTheDocument();
    expect(separator).toHaveAttribute("aria-pressed", "false");
    expect(separator).toHaveTextContent("AND");

    document.body.removeChild(anchorEl);
  });

  it("toggles to OR on click and re-applies the filters with 'or'", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    const { anchorEl } = renderPanel({ onApply });

    await user.click(screen.getByRole("tab", { name: "Query" }));
    await user.click(
      screen.getByRole("button", { name: "Combine filters with AND" }),
    );

    const or = screen.getByRole("button", { name: "Combine filters with OR" });
    expect(or).toHaveAttribute("aria-pressed", "true");
    expect(or).toHaveTextContent("OR");

    // The Query tab converts its tokens back to the API filter shape, so the
    // parent sees the AND/OR combinator alongside the same filters.
    expect(onApply).toHaveBeenCalledWith(
      { eval_type: ["llm"], output_type: ["pass_fail"] },
      "or",
    );

    document.body.removeChild(anchorEl);
  });

  it("does not show a separator for a single populated filter", async () => {
    const user = userEvent.setup();
    const { anchorEl } = renderPanel({
      currentFilters: { eval_type: ["llm"] },
    });

    await user.click(screen.getByRole("tab", { name: "Query" }));

    expect(
      screen.queryByRole("button", { name: /Combine filters with/i }),
    ).not.toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("hides the separator when showQueryCombinator is false (AND-only surface)", async () => {
    const user = userEvent.setup();
    const { anchorEl } = renderPanel({ showQueryCombinator: false });

    await user.click(screen.getByRole("tab", { name: "Query" }));

    expect(
      screen.queryByRole("button", { name: /Combine filters with/i }),
    ).not.toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });

  it("seeds the Query tab from currentCombinator on open (reopen shows OR)", async () => {
    const user = userEvent.setup();
    const { anchorEl } = renderPanel({ currentCombinator: "or" });

    await user.click(screen.getByRole("tab", { name: "Query" }));

    const or = screen.getByRole("button", { name: "Combine filters with OR" });
    expect(or).toBeInTheDocument();
    expect(or).toHaveAttribute("aria-pressed", "true");

    document.body.removeChild(anchorEl);
  });

  it("re-seeds to AND on reopen after OR was reset downstream", async () => {
    // Downstream reset the applied combinator back to "and" (e.g. Clear):
    // reopening the panel must show AND, not the stale OR from last session.
    const user = userEvent.setup();
    const { anchorEl, rerender } = renderPanel({
      currentCombinator: "and",
    });

    await user.click(screen.getByRole("tab", { name: "Query" }));
    expect(
      screen.getByRole("button", { name: "Combine filters with AND" }),
    ).toBeInTheDocument();

    rerender(
      <EvalFilterPanel
        anchorEl={anchorEl}
        open={false}
        onClose={vi.fn()}
        onApply={vi.fn()}
        currentFilters={{ eval_type: ["llm"], output_type: ["pass_fail"] }}
        lockedFilters={{}}
        currentCombinator="and"
      />,
    );
    rerender(
      <EvalFilterPanel
        anchorEl={anchorEl}
        open
        onClose={vi.fn()}
        onApply={vi.fn()}
        currentFilters={{ eval_type: ["llm"], output_type: ["pass_fail"] }}
        lockedFilters={{}}
        currentCombinator="and"
      />,
    );
    await user.click(screen.getByRole("tab", { name: "Query" }));
    expect(
      screen.getByRole("button", { name: "Combine filters with AND" }),
    ).toBeInTheDocument();

    document.body.removeChild(anchorEl);
  });
});
