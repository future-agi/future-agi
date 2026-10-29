import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

import FixScenariosTab from "../FixScenariosTab";

vi.mock("src/utils/axios", async (importOriginal) => {
  const { withFakeFixRunsApi } = await import(
    "src/api/simulate-environments/__tests__/fixtures/fixRunsApi"
  );
  return withFakeFixRunsApi(await importOriginal());
});

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
    // Call rows name a scenario by its key, as on the run page.
    expect(row.getByText("late-cancel")).toBeInTheDocument();
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
    expect(screen.getByRole("button", { name: "Broke 1" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Broke 1" }));
    expect(screen.getAllByTestId(/^scenario-/)).toHaveLength(5);
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
