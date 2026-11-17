import { afterEach, describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import axios from "src/utils/axios";
import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

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
const wrap = (ui) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );

// The fake server's read, so a test can answer only the patch read itself.
const realGet = axios.get.getMockImplementation();
const answerPatchReads = (answer) =>
  axios.get.mockImplementation((url, config) =>
    /\/patch\/$/.test(url) ? answer() : realGet(url, config),
  );
afterEach(() => {
  axios.get.mockImplementation(realGet);
});

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

  it("names the trial it shows and the version it changes", async () => {
    wrap(
      <FixChangesTab
        fix={byId("fix-ready-archive")}
        candidateId="fix-ready-archive-c2"
      />,
    );
    expect(
      await screen.findByText("Trial 2's change against agent v8"),
    ).toBeInTheDocument();
  });

  it("says when the change cannot be loaded, and loads it on Try again", async () => {
    const user = userEvent.setup();
    answerPatchReads(() =>
      Promise.reject({ statusCode: 500, detail: "Server error" }),
    );
    wrap(<FixChangesTab fix={byId("fix-ready-archive")} />);
    expect(
      await screen.findByText("Could not load Trial 3's change."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download patch" })).toBeNull();

    axios.get.mockImplementation(realGet);
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("instructions.md")).toBeInTheDocument();
    expect(screen.queryByText("Could not load Trial 3's change.")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Download patch" }),
    ).toBeInTheDocument();
  });

  it("says when the trial made no file changes", async () => {
    answerPatchReads(() => Promise.resolve({ data: "" }));
    wrap(<FixChangesTab fix={byId("fix-ready-archive")} />);
    expect(
      await screen.findByText("Trial 3 made no file changes."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download patch" })).toBeNull();
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

describe("Per scenario tab", () => {
  it("lists each scenario with its split, what the winner did and its check run", async () => {
    wrap(<FixScenariosTab fix={byId("fix-ready-archive")} />);
    const row = within(await screen.findByTestId("scenario-late-cancel"));
    expect(row.getByText("Target")).toBeInTheDocument();
    expect(row.getByText("Fixed")).toBeInTheDocument();
    expect(row.getByRole("link", { name: "Open run" })).toHaveAttribute(
      "href",
      "/dashboard/simulate/environments/env-1/runs/rt-fix-ready-archive/te-fix-ready-archive-c3",
    );
    expect(
      within(screen.getByTestId("scenario-on-time-cancel")).getByText("Broke"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("scenario-late-cancel-3")).getByText(
        "Held back",
      ),
    ).toBeInTheDocument();
  });

  it("filters to one outcome and back", async () => {
    const user = userEvent.setup();
    wrap(<FixScenariosTab fix={byId("fix-ready-archive")} />);
    await user.click(await screen.findByRole("button", { name: "Broke 1" }));
    expect(screen.getAllByTestId(/^scenario-/)).toHaveLength(1);
    expect(screen.getByTestId("scenario-on-time-cancel")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Broke 1" }));
    expect(screen.getAllByTestId(/^scenario-/)).toHaveLength(5);
  });
});
