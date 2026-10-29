import { describe, it, expect, vi, beforeEach } from "vitest";
import PropTypes from "prop-types";
import { render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { resetFixRunFixtures } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

vi.mock("src/utils/axios", async (importOriginal) => {
  const { withFakeFixRunsApi } = await import(
    "src/api/simulate-environments/__tests__/fixtures/fixRunsApi"
  );
  return withFakeFixRunsApi(await importOriginal());
});

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => vi.fn(),
}));

// DataGrid does not lay out rows in jsdom; a plain table shows which rows the
// page holds.
vi.mock("src/components/data-table", () => {
  function DataTable({ data }) {
    return (
      <table>
        <tbody>
          {data.map((row) => (
            <tr key={row.id} data-testid="fix-row">
              <td>{row.id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  DataTable.propTypes = { data: PropTypes.array };
  return { DataTable };
});

const axios = (await import("src/utils/axios")).default;
const { default: ImprovementsPanel } = await import("../ImprovementsPanel");

describe("ImprovementsPanel pages", () => {
  beforeEach(() => {
    resetFixRunFixtures();
    axios.get.mockClear();
  });

  it("walks the server's pages with the shared pager", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ImprovementsPanel env={{ id: "env-1" }} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("1–15 of 15")).toBeInTheDocument();
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Rows per page" }),
      "10",
    );
    expect(await screen.findByText("1–10 of 15")).toBeInTheDocument();
    expect(screen.getAllByTestId("fix-row")).toHaveLength(10);
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("11–15 of 15")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByTestId("fix-row")).toHaveLength(5));
    expect(axios.get).toHaveBeenLastCalledWith(
      "/simulate/api/agent-fix-runs/",
      { params: { environment_id: "env-1", page: 2, limit: 10 } },
    );
  });
});
