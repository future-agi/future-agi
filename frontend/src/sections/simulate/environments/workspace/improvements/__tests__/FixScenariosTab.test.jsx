import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/_fixtures/fixRuns";

import FixScenariosTab from "../FixScenariosTab";

const fix = {
  ...FIX_RUN_FIXTURES.find((f) => f.id === "fix-ready-archive"),
  environment_id: "env-1",
};

const renderTab = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <FixScenariosTab fix={fix} />
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe("Per scenario tab", () => {
  it("lists each scenario with its split and what the winner did to it", async () => {
    renderTab();
    const row = within(await screen.findByTestId("scenario-late-cancel"));
    expect(row.getByText("Late cancellation")).toBeInTheDocument();
    expect(row.getByText("Target")).toBeInTheDocument();
    expect(row.getByText("Fixed")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("scenario-on-time-cancel")).getByText("Broke"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("scenario-late-cancel-3")).getByText(
        "Held back",
      ),
    ).toBeInTheDocument();
  });

  it("filters to one outcome", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("button", { name: "Broke 1" }));
    expect(screen.getAllByTestId(/^scenario-/)).toHaveLength(1);
    expect(screen.getByTestId("scenario-on-time-cancel")).toBeInTheDocument();
  });

  it("opens the trial's check run for a scenario", async () => {
    renderTab();
    const link = within(
      await screen.findByTestId("scenario-late-cancel"),
    ).getByRole("link", { name: "Open run" });
    expect(link).toHaveAttribute(
      "href",
      "/dashboard/simulate/environments/env-1/runs/rt-fix-ready-archive/te-fix-ready-archive-c3",
    );
  });
});
