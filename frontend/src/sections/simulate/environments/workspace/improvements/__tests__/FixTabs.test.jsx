import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";
import axios from "src/utils/axios";

import FixChangesTab from "../FixChangesTab";
import FixCostTab from "../FixCostTab";
import FixScenariosTab from "../FixScenariosTab";

vi.mock("src/utils/axios", async (importOriginal) => {
  const { withFakeFixRunsApi } = await import(
    "src/api/simulate-environments/__tests__/fixtures/fixRunsApi"
  );
  return withFakeFixRunsApi(await importOriginal());
});

const byId = (id) => ({
  ...FIX_RUN_FIXTURES.find((f) => f.id === id),
  environment_id: "env-1",
});
// The Scenarios tab's badges and states read the theme's status colours.
const appTheme = createTheme({ palette: palette("light") });
const wrap = (ui) =>
  render(
    <ThemeProvider theme={appTheme}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );

describe("Changes tab", () => {
  it("shows the winner's change as a diff", async () => {
    wrap(<FixChangesTab fix={byId("fix-ready-archive")} />);
    expect(await screen.findByText("instructions.md")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Escalate the cancellation to the front desk and say you have done so.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Cancel the booking and confirm the refund."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Download patch" }),
    ).toBeInTheDocument();
  });

  it("shows the trial it was asked to review", async () => {
    wrap(
      <FixChangesTab
        fix={byId("fix-ready-archive")}
        candidateId="fix-ready-archive-c2"
      />,
    );
    expect(
      await screen.findByText("<!-- fix-ready-archive-c2 -->"),
    ).toBeInTheDocument();
  });
});

describe("Cost tab", () => {
  it("shows spent, committed and the budget as the server sends them", () => {
    render(<FixCostTab fix={byId("fix-ready-archive")} />);
    const cell = (label) => within(screen.getByTestId(`cost-${label}`));
    expect(cell("Spent").getByText("412 credits")).toBeInTheDocument();
    expect(cell("Committed").getByText("520 credits")).toBeInTheDocument();
    expect(cell("Budget").getByText("1,500 credits")).toBeInTheDocument();
    expect(screen.queryByTestId("cost-Reserved")).toBeNull();
  });
});

describe("Scenarios tab", () => {
  const rowIds = () =>
    screen.getAllByTestId(/^scenario-/).map((el) => el.dataset.testid);

  it("lists each scenario with its set and what the winner did, Broke first", async () => {
    wrap(<FixScenariosTab fix={byId("fix-ready-archive")} />);
    const row = within(await screen.findByTestId("scenario-late-cancel"));
    expect(row.getByText("Target")).toBeInTheDocument();
    expect(row.getByText("Fixed")).toBeInTheDocument();
    expect(row.getByText("3/3 trials")).toBeInTheDocument();
    // Held-back cases stay marked; the protected case reads Must-pass, and no
    // other row is guessed into it.
    expect(
      within(screen.getByTestId("scenario-late-cancel-3")).getByText(
        "Held back",
      ),
    ).toBeInTheDocument();
    const broke = within(screen.getByTestId("scenario-on-time-cancel"));
    expect(broke.getByText("Broke")).toBeInTheDocument();
    expect(broke.getByText("Must-pass")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("scenario-refund-window")).queryByText(
        "Must-pass",
      ),
    ).toBeNull();
    expect(rowIds()).toEqual([
      "scenario-on-time-cancel",
      "scenario-late-cancel",
      "scenario-late-cancel-2",
      "scenario-late-cancel-3",
      "scenario-refund-window",
    ]);
  });

  it("filters to one set and back, and puts Broke last on a second sort", async () => {
    const user = userEvent.setup();
    wrap(<FixScenariosTab fix={byId("fix-ready-archive")} />);
    await user.click(await screen.findByRole("tab", { name: "Held back 2" }));
    expect(rowIds()).toEqual([
      "scenario-late-cancel-2",
      "scenario-late-cancel-3",
    ]);
    await user.click(screen.getByRole("tab", { name: "All 5" }));
    expect(rowIds()).toHaveLength(5);
    expect(screen.getByRole("tab", { name: "Target 1" })).toBeInTheDocument();
    // No Must-pass filter: the read does not list the must-pass scenarios.
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "All 5",
      "Target 1",
      "Held back 2",
    ]);

    await user.click(screen.getByRole("button", { name: "Result" }));
    expect(rowIds().at(-1)).toBe("scenario-on-time-cancel");
  });

  it("falls back to All when the chosen set is left with no scenario", async () => {
    const user = userEvent.setup();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const tab = (fix) => (
      <ThemeProvider theme={appTheme}>
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <FixScenariosTab fix={fix} />
          </MemoryRouter>
        </QueryClientProvider>
      </ThemeProvider>
    );
    const fix = byId("fix-ready-archive");
    const { rerender } = render(tab(fix));
    await user.click(await screen.findByRole("tab", { name: "Held back 2" }));
    expect(rowIds()).toHaveLength(2);

    // The same calls, read with nothing held back: no Held back tab.
    rerender(
      tab({ ...fix, request: { ...fix.request, held_back_scenario_keys: [] } }),
    );
    expect(screen.queryByRole("tab", { name: /Held back/ })).toBeNull();
    expect(screen.getByRole("tab", { name: "All 5" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(rowIds()).toHaveLength(5);
  });

  it("opens a scenario's calls before the change beside the candidate's", async () => {
    const user = userEvent.setup();
    wrap(<FixScenariosTab fix={byId("fix-ready-archive")} />);
    await user.click(await screen.findByTestId("scenario-on-time-cancel"));
    const drawer = within(await screen.findByTestId("scenario-compare"));
    expect(
      drawer.getByText("Broke · before the change and with Candidate 3"),
    ).toBeInTheDocument();
    expect(drawer.getByText("Before the change")).toBeInTheDocument();
    expect(drawer.getByText("With Candidate 3")).toBeInTheDocument();
    const before = within(
      drawer.getByTestId(
        "compare-call-te-fix-ready-archive-baseline-on-time-cancel-1",
      ),
    );
    expect(before.getByText("Passed")).toBeInTheDocument();
    const after = within(
      drawer.getByTestId(
        "compare-call-te-fix-ready-archive-c3-on-time-cancel-1",
      ),
    );
    expect(after.getByText("Failed")).toBeInTheDocument();
    expect(after.getByRole("link", { name: "Open call" })).toHaveAttribute(
      "href",
      "/dashboard/simulate/environments/env-1/runs/rt-fix-ready-archive/te-fix-ready-archive-c3?rowId=te-fix-ready-archive-c3-on-time-cancel-1",
    );
  });

  it("says the results could not load, and why", async () => {
    const real = axios.get.getMockImplementation();
    axios.get.mockImplementation((url, config) =>
      /\/calls\/$/.test(url)
        ? Promise.reject({ statusCode: 500 })
        : real(url, config),
    );
    try {
      wrap(<FixScenariosTab fix={byId("fix-ready-archive")} />);
      expect(
        await screen.findByText("Could not load the scenario results"),
      ).toBeInTheDocument();
    } finally {
      axios.get.mockImplementation(real);
    }
  });
});
