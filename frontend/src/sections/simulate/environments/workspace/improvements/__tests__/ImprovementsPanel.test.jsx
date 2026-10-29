import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { render, screen, fireEvent, within } from "src/utils/test-utils";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/_fixtures/fixRuns";

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

const fixtures = (envId) =>
  FIX_RUN_FIXTURES.map((f) => ({ ...f, environment_id: envId }));
let hookValue;
vi.mock("src/api/simulate-environments/fixRuns", () => ({
  useEnvironmentFixRuns: () => hookValue,
}));

const { default: ImprovementsPanel } = await import("../ImprovementsPanel");

const env = { id: "env-1", name: "new env" };

describe("ImprovementsPanel", () => {
  beforeEach(() => {
    navigate.mockReset();
    hookValue = {
      fixes: fixtures("env-1"),
      count: FIX_RUN_FIXTURES.length,
      isLoading: false,
    };
  });

  it("shows one row per fix", () => {
    render(<ImprovementsPanel env={env} />);
    expect(screen.getAllByRole("row")).toHaveLength(FIX_RUN_FIXTURES.length);
  });

  it("names the fix and its agent", () => {
    render(<ImprovementsPanel env={env} />);
    const row = within(screen.getByTestId("row-fix-ready-archive"));
    expect(row.getByText("Improvement 10")).toBeInTheDocument();
    expect(row.getByText("Pickup dispatch agent · v8")).toBeInTheDocument();
    expect(row.getByText("Ready for review")).toBeInTheDocument();
  });

  it("shows the held-back result the way the design does", () => {
    render(<ImprovementsPanel env={env} />);
    const row = within(screen.getByTestId("row-fix-ready-archive"));
    expect(row.getByText("Fixed 2 of 2 held back")).toBeInTheDocument();
    expect(row.getByText("broke 1 held back")).toBeInTheDocument();
  });

  it("shows the hand-off: default when shipped, a pull request link when opened", () => {
    hookValue.fixes = hookValue.fixes.map((f) =>
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

  it("shows the cost in credits", () => {
    render(<ImprovementsPanel env={env} />);
    expect(
      within(screen.getByTestId("row-fix-ready-archive")).getByText(
        "412 credits",
      ),
    ).toBeInTheDocument();
  });

  it("never prints null or undefined for unknown identity", () => {
    render(<ImprovementsPanel env={env} />);
    const row = screen.getByTestId("row-fix-unknown-identity");
    expect(row.textContent).not.toMatch(/null|undefined/);
    expect(within(row).getByText("Improvement")).toBeInTheDocument();
  });

  it("opens the fix page", () => {
    render(<ImprovementsPanel env={env} />);
    fireEvent.click(screen.getByTestId("row-fix-ready-archive"));
    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/simulate/environments/env-1/improvements/fix-ready-archive",
    );
  });
});
