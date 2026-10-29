import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { FIX_RUN_FIXTURES } from "src/api/simulate-environments/_fixtures/fixRuns";

import FixChangesTab from "../FixChangesTab";
import FixCostTab from "../FixCostTab";

const byId = (id) => FIX_RUN_FIXTURES.find((f) => f.id === id);
const wrap = (ui) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {ui}
    </QueryClientProvider>,
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
    expect(screen.queryByText("Trial 3 · Winner")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Download patch" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).toBeNull();
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
  it("shows spent, reserved, committed and the budget side by side", () => {
    render(<FixCostTab fix={byId("fix-ready-archive")} />);
    const cell = (label) => within(screen.getByTestId(`cost-${label}`));
    expect(cell("Spent").getByText("412 credits")).toBeInTheDocument();
    expect(cell("Reserved").getByText("108 credits")).toBeInTheDocument();
    expect(cell("Committed").getByText("520 credits")).toBeInTheDocument();
    expect(cell("Budget").getByText("1,500 credits")).toBeInTheDocument();
  });
});
