import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within, userEvent } from "src/utils/test-utils";

import EnvironmentsListView from "../EnvironmentsListView";

// Fixed clock so the relative "Updated" column is deterministic.
const NOW = new Date("2026-08-18T12:00:00.000Z");
const nowSeconds = NOW.getTime() / 1000;

const rows = [
  {
    session_id: "sess_all_passed",
    agent: "Drive-Thru Voice Agent",
    title: "Drive-thru ordering",
    one_liner: "Takes burger orders at the window.",
    created: nowSeconds - 86400,
    updated: nowSeconds - 3600, // an hour ago
    tools: 7,
    sub_goals: 5,
    scenarios: 14,
    runs: 14,
    runs_passed: 14,
    run_test_id: "rt_1",
    execution_id: "ex_1",
  },
  {
    session_id: "sess_some_failed",
    agent: "Tier-1 Support Bot",
    title: "Billing support",
    one_liner: "Answers billing questions and issues small refunds.",
    created: nowSeconds - 172800,
    updated: nowSeconds - 7200,
    tools: 12,
    sub_goals: 8,
    scenarios: 26,
    runs: 26,
    runs_passed: 21,
    run_test_id: "rt_2",
    execution_id: "ex_2",
  },
  {
    session_id: "sess_never_run",
    agent: null,
    title: "Grocery substitution picker",
    one_liner: "Picks substitutes for out-of-stock items.",
    created: nowSeconds - 3600,
    updated: nowSeconds - 1800,
    tools: 3,
    sub_goals: 2,
    scenarios: 5,
    runs: 0,
    runs_passed: 0,
    run_test_id: null,
    execution_id: null,
  },
];

// Scoped to the chip: the count it shows can legitimately equal another column's number,
// so a document-wide text lookup would match the wrong cell.
const chipTone = (label) =>
  screen
    .getAllByTestId("runs-chip")
    .find((chip) => chip.textContent.trim() === String(label))?.dataset.tone;

describe("EnvironmentsListView", () => {
  it("offers only one way to create when there is nothing yet", () => {
    render(<EnvironmentsListView environments={[]} onAdd={vi.fn()} onOpen={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /add environment/i })).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /create your first environment/i })
    ).toBeInTheDocument();
  });

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders a row per environment with its counts", () => {
    render(<EnvironmentsListView environments={rows} />);

    expect(screen.getByText("Drive-Thru Voice Agent")).toBeInTheDocument();
    expect(screen.getByText("Tier-1 Support Bot")).toBeInTheDocument();
    // No agent name on the third row, so the title is used.
    expect(screen.getByText("Grocery substitution picker")).toBeInTheDocument();

    expect(
      screen.getByText("Takes burger orders at the window."),
    ).toBeInTheDocument();

    const row = screen
      .getByText("Drive-Thru Voice Agent")
      .closest("[role='row']");
    expect(within(row).getByText("7")).toBeInTheDocument(); // tools
    expect(within(row).getByText("5")).toBeInTheDocument(); // sub-goals
    // Scoped away from the runs chip, which can carry the same number.
    expect(
      within(row)
        .getAllByText("14")
        .some((el) => !el.closest("[data-testid='runs-chip']")),
    ).toBe(true); // scenarios
  });

  it("keeps the full description available as a tooltip", () => {
    render(<EnvironmentsListView environments={rows} />);

    expect(
      screen.getByText("Takes burger orders at the window."),
    ).toHaveAttribute("title", "Takes burger orders at the window.");
  });

  it("shows how many runs there are, coloured by outcome", () => {
    render(<EnvironmentsListView environments={rows} />);

    // The chip counts runs; a fraction here could only ever describe one of them.
    expect(chipTone("14")).toBe("pass");
    // Some passed, some did not — progress, not failure, so it must not wear the failure
    // colour. Only an environment where nothing passed has actually failed.
    expect(chipTone("26")).toBe("partial");
    expect(chipTone("No runs")).toBe("neutral");
    expect(screen.queryByText("14/14")).not.toBeInTheDocument();
    expect(screen.queryByText("21/26")).not.toBeInTheDocument();
  });

  it("renders the updated time as a relative distance", () => {
    render(<EnvironmentsListView environments={rows} />);

    // `updated` is epoch seconds — a raw `new Date(seconds)` would read as 1970.
    expect(screen.getByText("about 1 hour ago")).toBeInTheDocument();
    expect(screen.getByText("30 minutes ago")).toBeInTheDocument();
  });

  it("filters on name and description as the user types", async () => {
    const user = userEvent.setup();
    render(<EnvironmentsListView environments={rows} />);

    await user.type(screen.getByPlaceholderText("Search"), "support");
    expect(screen.getByText("Tier-1 Support Bot")).toBeInTheDocument();
    expect(
      screen.queryByText("Drive-Thru Voice Agent"),
    ).not.toBeInTheDocument();

    await user.clear(screen.getByPlaceholderText("Search"));
    // Matches the one_liner, not the name.
    await user.type(screen.getByPlaceholderText("Search"), "out-of-stock");
    expect(screen.getByText("Grocery substitution picker")).toBeInTheDocument();
    expect(screen.queryByText("Tier-1 Support Bot")).not.toBeInTheDocument();
  });

  it("calls onOpen with the session id when a row is clicked", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<EnvironmentsListView environments={rows} onOpen={onOpen} />);

    await user.click(screen.getByText("Tier-1 Support Bot"));

    expect(onOpen).toHaveBeenCalledWith("sess_some_failed");
  });

  it("calls onAdd from the Add Environment button", async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    render(<EnvironmentsListView environments={rows} onAdd={onAdd} />);

    await user.click(screen.getByRole("button", { name: /add environment/i }));

    expect(onAdd).toHaveBeenCalledTimes(1);
  });

  it("invites a first environment when there are none", async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    render(<EnvironmentsListView environments={[]} onAdd={onAdd} />);

    expect(screen.getByText("No environments yet")).toBeInTheDocument();
    // Nothing to search through, so the search field is hidden.
    expect(screen.queryByPlaceholderText("Search")).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: /create your first environment/i }),
    );
    expect(onAdd).toHaveBeenCalledTimes(1);
  });

  it("reports an empty search separately from an empty list", async () => {
    const user = userEvent.setup();
    render(<EnvironmentsListView environments={rows} />);

    await user.type(
      screen.getByPlaceholderText("Search"),
      "nothing matches me",
    );

    expect(
      screen.getByText("No environments match your search"),
    ).toBeInTheDocument();
    expect(screen.queryByText("No environments yet")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Drive-Thru Voice Agent"),
    ).not.toBeInTheDocument();
  });
});
