import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";

import { QueryInput } from "../FilterPanel";

// The AND/OR separator lives between the query-builder chips. One value for
// the whole list, so toggling any separator updates them all and re-applies
// through onApply(tokens, combinator).

const FIELD_MAP = {
  status: { label: "Status", type: "enum", choices: ["ERROR", "OK"] },
  model: { label: "Model", type: "string" },
};

const FILTER_FIELDS = [
  { value: "status", label: "Status", type: "enum" },
  { value: "model", label: "Model", type: "string" },
];

const TWO_TOKENS = [
  { field: "status", operator: "is", value: "ERROR" },
  { field: "model", operator: "contains", value: "gpt" },
];

const THREE_TOKENS = [
  ...TWO_TOKENS,
  { field: "model", operator: "contains", value: "gpt-4" },
];

function renderInput({
  tokens = TWO_TOKENS,
  onApply = vi.fn(),
  showCombinator = true,
  initialCombinator,
} = {}) {
  const props = {
    filterFields: FILTER_FIELDS,
    fieldMap: FIELD_MAP,
    onApply,
    initialTokens: tokens,
    showCombinator,
  };
  if (initialCombinator) props.initialCombinator = initialCombinator;
  return render(<QueryInput {...props} />);
}

describe("QueryInput AND/OR combinator (#2226)", () => {
  it("shows a separator between two chips and none at one token", () => {
    const { unmount } = renderInput();
    expect(
      screen.getByRole("button", { name: "Combine filters with AND" }),
    ).toBeInTheDocument();
    unmount();

    renderInput({ tokens: [TWO_TOKENS[0]] });
    expect(
      screen.queryByRole("button", { name: /Combine filters with/i }),
    ).not.toBeInTheDocument();
  });

  it("is not rendered unless showCombinator is enabled", () => {
    renderInput({ showCombinator: false });
    expect(
      screen.queryByRole("button", { name: /Combine filters with/i }),
    ).not.toBeInTheDocument();
  });

  it("exposes the AND state via aria-label and aria-pressed", () => {
    renderInput();
    const separator = screen.getByRole("button", {
      name: "Combine filters with AND",
    });
    expect(separator).toHaveAttribute("aria-pressed", "false");
    expect(separator).toHaveTextContent("AND");
  });

  it("toggles to OR on click and re-applies with 'or'", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    renderInput({ onApply });

    await user.click(
      screen.getByRole("button", { name: "Combine filters with AND" }),
    );

    const or = screen.getByRole("button", { name: "Combine filters with OR" });
    expect(or).toHaveAttribute("aria-pressed", "true");
    expect(or).toHaveTextContent("OR");
    expect(onApply).toHaveBeenCalledWith(TWO_TOKENS, "or");
  });

  it("activates on Enter and on Space", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    renderInput({ onApply });

    const separator = screen.getByRole("button", {
      name: "Combine filters with AND",
    });

    separator.focus();
    await user.keyboard("{Enter}");
    expect(
      screen.getByRole("button", { name: "Combine filters with OR" }),
    ).toBeInTheDocument();
    expect(onApply).toHaveBeenCalledWith(TWO_TOKENS, "or");

    await user.keyboard(" ");
    expect(
      screen.getByRole("button", { name: "Combine filters with AND" }),
    ).toBeInTheDocument();
    expect(onApply).toHaveBeenLastCalledWith(TWO_TOKENS, "and");
  });

  it("updates every separator together across the whole query", async () => {
    const user = userEvent.setup();
    renderInput({ tokens: THREE_TOKENS });

    // Three tokens -> two separators, both AND.
    expect(
      screen.getAllByRole("button", { name: "Combine filters with AND" }),
    ).toHaveLength(2);

    await user.click(
      screen.getAllByRole("button", { name: "Combine filters with AND" })[0],
    );

    expect(
      screen.getAllByRole("button", { name: "Combine filters with OR" }),
    ).toHaveLength(2);
    expect(
      screen.queryByRole("button", { name: "Combine filters with AND" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the filter chips rendered next to the separator", () => {
    renderInput();
    // Both completed tokens still render as chips alongside the separator.
    expect(screen.getByText(/Status/)).toBeInTheDocument();
    expect(screen.getByText(/Model/)).toBeInTheDocument();
  });

  it("seeds from initialCombinator so a re-opened panel shows the applied operator", () => {
    // A panel reopened after the user picked OR must render OR — otherwise
    // the UI shows AND while the grid keeps filtering by OR.
    renderInput({ initialCombinator: "or" });
    expect(
      screen.getByRole("button", { name: "Combine filters with OR" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Combine filters with AND" }),
    ).not.toBeInTheDocument();
  });

  it("toggling from a seeded OR state applies 'and' again", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    renderInput({ onApply, initialCombinator: "or" });

    await user.click(
      screen.getByRole("button", { name: "Combine filters with OR" }),
    );

    expect(
      screen.getByRole("button", { name: "Combine filters with AND" }),
    ).toBeInTheDocument();
    expect(onApply).toHaveBeenCalledWith(TWO_TOKENS, "and");
  });
});
