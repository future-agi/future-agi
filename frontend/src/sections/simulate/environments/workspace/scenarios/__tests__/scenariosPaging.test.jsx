import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import { generatedPool } from "src/api/simulate-environments/_fixtures/scenarioPool";
import ScenariosStep from "../ScenariosStep";
import { PAGE_SIZE } from "../useScenarioPage";

const { mockSnack } = vi.hoisted(() => ({ mockSnack: { calls: [], close: () => {} } }));
vi.mock("notistack", () => ({
  useSnackbar: () => ({
    enqueueSnackbar: (message, options) => { mockSnack.calls.push({ message, options }); return "snack-key"; },
    closeSnackbar: (...args) => mockSnack.close(...args),
  }),
}));

const env = MOCK_WORLD;
// 60 rows (> two pages at PAGE_SIZE 25) with unique ids so the pager engages on
// a normal, un-inflated env — the real adopted path, not the ?scnDemo harness.
const pool = generatedPool(env);
const many = Array.from({ length: 60 }, (_, i) => {
  const base = pool[i % pool.length];
  return { ...base, id: `s-${i}`, name: `${base.name || base.title} ${i}` };
});

const renderStep = (scenarios = many) => {
  const patch = vi.fn();
  render(<ScenariosStep env={env} envState={{ scenarios }} patch={patch} />);
  return { patch };
};

const headerCheckbox = () => screen.getAllByRole("checkbox")[0];

beforeEach(() => { mockSnack.calls = []; });

describe("ScenariosStep — pagination", () => {
  it("shows one page of rows with a pager and range", () => {
    renderStep();
    expect(screen.getByText(/Showing 1–25 of 60/)).toBeInTheDocument();
    // header + PAGE_SIZE row checkboxes on the page
    expect(screen.getAllByRole("checkbox")).toHaveLength(PAGE_SIZE + 1);
    expect(screen.getByLabelText("Go to page 2")).toBeInTheDocument();
  });

  it("advances to the next page and shifts the range", () => {
    renderStep();
    fireEvent.click(screen.getByLabelText("Go to page 2"));
    expect(screen.getByText(/Showing 26–50 of 60/)).toBeInTheDocument();
  });
});

describe("ScenariosStep — select all matching", () => {
  it("header selects only the page, then the banner escalates to the whole match", () => {
    renderStep();
    fireEvent.click(headerCheckbox());

    // Page selected → the banner offers the escalation to every match.
    expect(screen.getByRole("button", { name: /Select all 60 matching/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Select all 60 matching/ }));
    // Escalated: the whole-match line replaces the link, and there is a single
    // Clear (the bar's ✕), not a duplicate in a second banner.
    expect(screen.getByText(/All 60 matching scenarios selected/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Select all 60 matching/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Clear selection" })).toHaveLength(1);
  });

  it("keeps the whole-match selection across a page change without loading every row", () => {
    renderStep();
    fireEvent.click(headerCheckbox());
    fireEvent.click(screen.getByRole("button", { name: /Select all 60 matching/ }));

    fireEvent.click(screen.getByLabelText("Go to page 2"));
    expect(screen.getByText(/Showing 26–50 of 60/)).toBeInTheDocument();
    // Every checkbox on the new page is checked — the predicate covers rows the
    // client never loaded on page 1.
    screen.getAllByRole("checkbox").forEach((c) => expect(c).toBeChecked());
  });

  it("bulk-deletes the whole match through patch", () => {
    const { patch } = renderStep();
    fireEvent.click(headerCheckbox());
    fireEvent.click(screen.getByRole("button", { name: /Select all 60 matching/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Delete/ }));

    // All 60 matched → the store is emptied in one predicate delete.
    expect(patch).toHaveBeenCalledWith({ scenarios: [] });
  });

  it("un-checking a row in all-mode drops one from the count (an exclusion)", () => {
    renderStep();
    fireEvent.click(headerCheckbox());
    fireEvent.click(screen.getByRole("button", { name: /Select all 60 matching/ }));
    // Uncheck the first row checkbox (index 1; 0 is the header).
    fireEvent.click(screen.getAllByRole("checkbox")[1]);
    expect(screen.getByText(/All 59 matching scenarios selected/)).toBeInTheDocument();
  });
});

describe("ScenariosStep — list view has checkboxes", () => {
  it("renders per-row and per-group checkboxes in the list view", () => {
    renderStep();
    fireEvent.click(screen.getByRole("tab", { name: "List" }));
    // At least one group checkbox and the page's row checkboxes are present.
    expect(screen.getAllByLabelText(/^Select group/).length).toBeGreaterThan(0);
    const rowBoxes = screen.getAllByLabelText(/^Select (?!group)/);
    expect(rowBoxes.length).toBeGreaterThan(0);
  });
});
