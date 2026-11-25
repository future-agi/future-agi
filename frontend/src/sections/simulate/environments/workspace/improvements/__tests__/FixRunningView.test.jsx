import { describe, it, expect, vi } from "vitest";
import { render as rtlRender, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { MemoryRouter } from "react-router-dom";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

import FixRunningView from "../FixRunningView";
import TrialChart from "../TrialChart";

// The trial chart reads theme.palette.data, so render with the app theme.
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
    expect(screen.getByTestId("candidate-2")).toHaveTextContent("Running");
    expect(screen.getAllByTestId("trial-point")).toHaveLength(1);
    expect(
      screen.getByText(
        "2 of up to 4 candidates · best so far 0% on 6 target scenarios",
      ),
    ).toBeInTheDocument();

    fix.candidates[0].verdict.target_pass_rate = null;
    const { container } = render(<TrialChart candidates={fix.candidates} />);
    expect(container.querySelector("svg")).toBeNull();
  });
});

describe("the running view's candidates", () => {
  it("uses the finished page's table: the candidate being checked leads, open", async () => {
    const user = userEvent.setup();
    renderView(byId("fix-search"));
    expect(
      screen.getAllByTestId(/^candidate-group-/).map((g) => g.dataset.testid),
    ).toEqual(["candidate-group-pending", "candidate-group-target"]);
    const running = within(screen.getByTestId("candidate-2"));
    expect(running.getByText("Running")).toBeInTheDocument();
    expect(running.getByText("↑ 34 pts")).toBeInTheDocument();
    expect(running.getByRole("link", { name: "Open run" })).toHaveAttribute(
      "href",
      "/dashboard/simulate/environments/env-1/runs/rt-fix-search/te-fix-search-c2",
    );
    // A rejected candidate sits in its check's group, folded as it is once
    // the epoch ends.
    expect(screen.queryByTestId("candidate-1")).toBeNull();
    await user.click(screen.getByTestId("candidate-group-target"));
    const rejected = within(screen.getByTestId("candidate-1"));
    expect(
      rejected.getByText("Escalate refused late cancellations to the front desk."),
    ).toBeInTheDocument();
    expect(rejected.getByText("0%")).toBeInTheDocument();
  });

  it("says the agent was measured automatically only when the platform says so", () => {
    const measured = byId("fix-search");
    measured.request.measured_automatically = true;
    const { unmount } = renderView(measured);
    expect(screen.getByTestId("measured-notice")).toHaveTextContent(
      "Measured automatically: model, tokens and timing only. Prompts and replies are never recorded.",
    );
    unmount();

    // A read without the field, or with it false, shows no notice.
    const missing = byId("fix-search");
    delete missing.request.measured_automatically;
    renderView(missing);
    expect(screen.queryByTestId("measured-notice")).toBeNull();
  });
});

describe("TrialChart", () => {
  it("draws one point per trial and labels the best", () => {
    render(<TrialChart candidates={byId("fix-ready-archive").candidates} />);
    expect(screen.getAllByTestId("trial-point")).toHaveLength(3);
    expect(
      screen.getByText(
        /^pass rate on the target scenarios · Candidate 3 · winner/,
      ),
    ).toBeInTheDocument();
  });

  it("shows no arrow when the best trial did not move the pass rate", () => {
    const [trial] = byId("fix-search").candidates;
    trial.verdict.target_pass_rate = [0.5, 0.5];
    render(<TrialChart candidates={[trial]} />);
    const lead = screen.getByText(/^pass rate on the target scenarios/);
    expect(lead).toHaveTextContent(
      "Candidate 1 · best so far · no change from the agent before the change, on the same scenarios",
    );
    expect(lead).not.toHaveTextContent(/[▲▼]/);
  });
});
