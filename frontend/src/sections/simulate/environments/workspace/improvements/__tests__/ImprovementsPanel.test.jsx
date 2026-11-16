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
  function DataTable({ columns, data, onRowClick, emptyMessage }) {
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
  };
  return { DataTable };
});

// One server page of the fixtures for the 0-based page the panel asks for.
let allFixes;
vi.mock("src/api/simulate-environments/fixRuns", () => ({
  FIX_RUNS_PAGE_SIZE: 25,
  useEnvironmentFixRuns: (_envId, { page, pageSize }) => ({
    fixes: allFixes.slice(page * pageSize, (page + 1) * pageSize),
    count: allFixes.length,
    isLoading: false,
  }),
}));

const { default: ImprovementsPanel } = await import("../ImprovementsPanel");

const env = { id: "env-1", name: "new env" };

describe("ImprovementsPanel", () => {
  beforeEach(() => {
    navigate.mockReset();
    allFixes = FIX_RUN_FIXTURES.map((f) => ({ ...f, environment_id: "env-1" }));
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
