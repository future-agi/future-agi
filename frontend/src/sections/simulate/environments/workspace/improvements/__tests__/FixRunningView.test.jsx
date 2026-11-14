import { describe, it, expect, vi } from "vitest";
import { render as rtlRender, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { MemoryRouter } from "react-router-dom";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

import FixRunningView from "../FixRunningView";
import TrialChart from "../TrialChart";
import TrialsTable from "../TrialsTable";

// The trials table reads theme.palette.data, so render with the app theme.
const appTheme = createTheme({ palette: palette("light") });
// eslint-disable-next-line react/prop-types
const AppTheme = ({ children }) => (
  <ThemeProvider theme={appTheme}>{children}</ThemeProvider>
);
const render = (ui, options) =>
  rtlRender(ui, { wrapper: AppTheme, ...options });

vi.mock("src/utils/axios", async (importOriginal) => {
  const { withFakeFixRunsApi } = await import(
    "src/api/simulate-environments/__tests__/fixtures/fixRunsApi"
  );
  return withFakeFixRunsApi(await importOriginal());
});

const byId = (id) => ({
  ...structuredClone(FIX_RUN_FIXTURES.find((f) => f.id === id)),
  environment_id: "env-1",
});

const renderView = (fix, props = {}) =>
  render(
    <MemoryRouter>
      <FixRunningView fix={fix} {...props} />
    </MemoryRouter>,
  );

describe("FixRunningView", () => {
  it("links the reproduce step to the run it starts from", () => {
    renderView(byId("fix-search"), { runTestId: "rt-env" });
    expect(
      within(screen.getByTestId("step-reproduce")).getByRole("link", {
        name: "Environment Run 1",
      }),
    ).toHaveAttribute(
      "href",
      "/dashboard/simulate/environments/env-1/runs/rt-env/te-source-run-1",
    );
  });

  it("renders trials whose verdict or rate has not arrived", () => {
    const fix = byId("fix-search");
    fix.candidates[1].verdict = null;
    renderView(fix);
    expect(screen.getByTestId("trial-2")).toHaveTextContent("Pending");
    expect(screen.getAllByTestId("trial-point")).toHaveLength(1);
    expect(
      screen.getByText(
        "2 of up to 4 trials · best so far 0% on 6 target scenarios",
      ),
    ).toBeInTheDocument();

    fix.candidates[0].verdict.target_pass_rate = null;
    const { container } = render(<TrialChart candidates={fix.candidates} />);
    expect(container.querySelector("svg")).toBeNull();
  });
});

describe("TrialsTable", () => {
  it("lists trials with what they tried, status, pass rate and its change", () => {
    renderView(byId("fix-search"));
    const row = within(screen.getByTestId("trial-1"));
    expect(
      row.getByText(
        "Tried: Escalate refused late cancellations to the front desk.",
      ),
    ).toBeInTheDocument();
    expect(row.getByText("Rejected")).toBeInTheDocument();
    expect(row.getByText("0%")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("trial-2")).getByText("↑ 34 pts"),
    ).toBeInTheDocument();
  });

  it("links a running trial to its live check run", () => {
    renderView(byId("fix-search"));
    expect(
      within(screen.getByTestId("trial-2")).getByRole("link", {
        name: "Running · Watch live",
      }),
    ).toHaveAttribute(
      "href",
      "/dashboard/simulate/environments/env-1/runs/rt-fix-search/te-fix-search-c2",
    );
  });

  it("reads Did not run, with no Watch live, for a pending trial on a stopped fix", () => {
    const fix = { ...byId("fix-search"), state: "stopped", stage: null };
    render(
      <MemoryRouter>
        <TrialsTable fix={fix} />
      </MemoryRouter>,
    );
    const trial = screen.getByTestId("trial-2");
    expect(trial).toHaveTextContent("Did not run");
    expect(trial).not.toHaveTextContent("Watch live");
  });
});

describe("TrialChart", () => {
  it("draws one point per trial and labels the best", () => {
    render(<TrialChart candidates={byId("fix-ready-archive").candidates} />);
    expect(screen.getAllByTestId("trial-point")).toHaveLength(3);
    expect(
      screen.getByText(/^pass rate on the target scenarios · Trial 3 · winner/),
    ).toBeInTheDocument();
  });

  it("shows no arrow when the best trial did not move the pass rate", () => {
    const [trial] = byId("fix-search").candidates;
    trial.verdict.target_pass_rate = [0.5, 0.5];
    render(<TrialChart candidates={[trial]} />);
    const lead = screen.getByText(/^pass rate on the target scenarios/);
    expect(lead).toHaveTextContent(
      "Trial 1 · best so far · no change from the agent before the change, on the same scenarios",
    );
    expect(lead).not.toHaveTextContent(/[▲▼]/);
  });
});
