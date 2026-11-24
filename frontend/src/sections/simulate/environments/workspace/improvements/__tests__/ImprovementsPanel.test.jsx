import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { render, screen, fireEvent, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

const navigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => navigate,
}));

// DataGrid does not lay out rows in jsdom; render the real column cells in a
// plain table instead, so the assertions are about our columns.
vi.mock("src/components/data-table", () => {
  function DataTable({ columns, data, onRowClick, emptyMessage, isLoading }) {
    if (isLoading) return <p>Loading rows</p>;
    if (!data.length) return <p>{emptyMessage}</p>;
    return (
      <table>
        <tbody>
          {data.map((row) => (
            <tr
              key={row.id}
              data-testid={`row-${row.id}`}
              onClick={() => onRowClick(row)}
            >
              {columns.map((c) => (
                <td key={c.id}>{c.cell({ row: { original: row } })}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  DataTable.propTypes = {
    columns: PropTypes.array,
    data: PropTypes.array,
    onRowClick: PropTypes.func,
    emptyMessage: PropTypes.string,
    isLoading: PropTypes.bool,
  };
  return { DataTable };
});

// The server, as the list sees it: the fixes in the asked states, one 0-based
// page of them and their total, and 404 for a page past the end. `asked`
// records the states of each read; `loading` holds a read in flight.
let allFixes;
let loading = false;
let failing = null;
const asked = [];
vi.mock("src/api/simulate-environments/fixRuns", () => ({
  FIX_RUNS_PAGE_SIZE: 25,
  FIX_LIVE_STATES: new Set(["queued", "running", "stopping"]),
  isNotFound: (error) => error?.statusCode === 404,
  useEnvironmentFixRuns: (_envId, { page, pageSize, states = [] } = {}) => {
    asked.push(states.join(","));
    if (loading) return { fixes: [], count: 0, isLoading: true, error: null };
    if (failing)
      return { fixes: [], count: 0, isLoading: false, error: failing };
    const matched = allFixes.filter(
      (f) => !states.length || states.includes(f.state),
    );
    if (page > 0 && page * pageSize >= matched.length) {
      return {
        fixes: [],
        count: 0,
        isLoading: false,
        error: { statusCode: 404 },
      };
    }
    return {
      fixes: matched.slice(page * pageSize, (page + 1) * pageSize),
      count: matched.length,
      isLoading: false,
      error: null,
    };
  },
}));

const { default: ImprovementsPanel } = await import("../ImprovementsPanel");

const env = { id: "env-1", name: "new env" };

describe("ImprovementsPanel", () => {
  beforeEach(() => {
    navigate.mockReset();
    asked.length = 0;
    loading = false;
    failing = null;
    allFixes = FIX_RUN_FIXTURES.map((f) => ({ ...f, environment_id: "env-1" }));
  });

  const chooseStatus = async (user, label) => {
    await user.click(screen.getByRole("combobox", { name: "Status" }));
    await user.click(await screen.findByRole("option", { name: label }));
  };

  const stoppedFixes = (n) =>
    Array.from({ length: n }, (_, i) => ({
      ...FIX_RUN_FIXTURES.find((f) => f.id === "fix-stopped"),
      id: `stopped-${i}`,
      number: 100 + i,
      environment_id: "env-1",
    }));

  it("asks the server for the status's states, and pages its total", async () => {
    const user = userEvent.setup();
    allFixes = stoppedFixes(30).concat(
      allFixes.filter(
        (f) => f.id === "fix-ready-archive" || f.id === "fix-failed",
      ),
    );
    render(<ImprovementsPanel env={env} />);
    expect(asked.at(-1)).toBe("");
    expect(screen.getByText("1–25 of 32")).toBeInTheDocument();

    await chooseStatus(user, "Ready for review");
    expect(asked.at(-1)).toBe("ready");
    expect(screen.getAllByTestId(/^row-/).map((r) => r.dataset.testid)).toEqual(
      ["row-fix-ready-archive"],
    );
    expect(screen.getByText("1–1 of 1")).toBeInTheDocument();

    await chooseStatus(user, "Stopped");
    expect(screen.getAllByTestId(/^row-/)).toHaveLength(25);
    expect(screen.getByText("1–25 of 30")).toBeInTheDocument();
  });

  it("steps back to a page that exists when a poll shrinks the count", async () => {
    const user = userEvent.setup();
    allFixes = stoppedFixes(30);
    const { rerender } = render(<ImprovementsPanel env={env} />);
    await chooseStatus(user, "Stopped");
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText("26–30 of 30")).toBeInTheDocument();

    // Ten of them left the status: the second page is past the end now.
    allFixes = stoppedFixes(20);
    rerender(<ImprovementsPanel env={env} />);
    expect(await screen.findByText("1–20 of 20")).toBeInTheDocument();
    expect(screen.queryByText(/has the status/)).toBeNull();
  });

  it("reads queued, running and stopping as one Running", async () => {
    const user = userEvent.setup();
    render(<ImprovementsPanel env={env} />);
    await chooseStatus(user, "Running");
    expect(asked.at(-1)).toBe("queued,running,stopping");
    const shown = screen.getAllByTestId(/^row-/).map((r) => r.dataset.testid);
    expect(shown).toEqual(
      expect.arrayContaining([
        "row-fix-queued",
        "row-fix-search",
        "row-fix-stopping",
      ]),
    );
    expect(shown).not.toContain("row-fix-ready-archive");
  });

  it("says why the list is empty when no epoch has the status", async () => {
    const user = userEvent.setup();
    allFixes = allFixes.filter((f) => f.state !== "too_few_cases");
    render(<ImprovementsPanel env={env} />);
    await chooseStatus(user, "Too few failing scenarios");
    expect(
      screen.getByText(
        "No epoch in this environment has the status “Too few failing scenarios”. Choose All statuses to see every epoch.",
      ),
    ).toBeInTheDocument();
  });

  it("says the list failed to load, since no toast says it", () => {
    failing = { statusCode: 500 };
    render(<ImprovementsPanel env={env} />);
    expect(
      screen.getByText(
        "Could not load the epochs. Reload the page to try again.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No epochs have run/)).toBeNull();
  });

  it("says nothing is missing while the filtered page is still loading", async () => {
    const user = userEvent.setup();
    render(<ImprovementsPanel env={env} />);
    loading = true;
    await chooseStatus(user, "Stopped");
    expect(screen.getByText("Loading rows")).toBeInTheDocument();
    expect(screen.queryByText(/has the status/)).toBeNull();
  });

  it("shows a fix's name, agent, state, held-back result and cost", () => {
    render(<ImprovementsPanel env={env} />);
    const row = within(screen.getByTestId("row-fix-ready-archive"));
    expect(row.getByText("Epoch 10")).toBeInTheDocument();
    expect(row.getByText("Pickup dispatch agent · v8")).toBeInTheDocument();
    expect(row.getByText("Ready for review")).toBeInTheDocument();
    expect(row.getByText("Held back 17% → 83%")).toBeInTheDocument();
    // It counts must-pass regressions, so it says so.
    expect(row.getByText("broke 1 must-pass")).toBeInTheDocument();
    expect(row.getByText("412 credits")).toBeInTheDocument();
  });

  it("shows the hand-off: default when shipped, a pull request link when opened", () => {
    allFixes = allFixes.map((f) =>
      f.id === "fix-ready-github"
        ? {
            ...f,
            candidates: f.candidates.map((c) =>
              c.outcome === "winner"
                ? {
                    ...c,
                    pull_request_url:
                      "https://github.com/acme/dispatch-agent/pull/42",
                  }
                : c,
            ),
          }
        : f,
    );
    render(<ImprovementsPanel env={env} />);
    expect(
      within(screen.getByTestId("row-fix-ready-shipped")).getByText("Default"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("row-fix-ready-github")).getByRole("link", {
        name: "Pull request",
      }),
    ).toHaveAttribute("href", "https://github.com/acme/dispatch-agent/pull/42");
  });

  it("never prints null or undefined for unknown identity", () => {
    render(<ImprovementsPanel env={env} />);
    const row = screen.getByTestId("row-fix-unknown-identity");
    expect(row.textContent).not.toMatch(/null|undefined/);
    expect(within(row).getByText("Epoch")).toBeInTheDocument();
  });

  it("opens the fix page", () => {
    render(<ImprovementsPanel env={env} />);
    fireEvent.click(screen.getByTestId("row-fix-ready-archive"));
    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/env-1/improvements/fix-ready-archive",
    );
  });

  it("walks the server's pages with the shared pager", async () => {
    const user = userEvent.setup();
    render(<ImprovementsPanel env={env} />);
    expect(screen.getByText("1–15 of 15")).toBeInTheDocument();
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Rows per page" }),
      "10",
    );
    expect(screen.getByText("1–10 of 15")).toBeInTheDocument();
    expect(screen.getAllByTestId(/^row-/)).toHaveLength(10);
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText("11–15 of 15")).toBeInTheDocument();
    expect(screen.getAllByTestId(/^row-/)).toHaveLength(5);
  });
});
