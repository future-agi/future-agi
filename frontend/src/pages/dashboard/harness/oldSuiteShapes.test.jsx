import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

import StageOutput from "./StageOutput";

vi.mock("src/api/harness/harness", () => ({
  amendHarnessScenarios: vi.fn(),
}));

// Suites written before today still have to open.
//
// The scenario document has grown four times: the oldest jobs in the database carry seven fields,
// then eighteen once personas arrived, then twenty, and only jobs authored from 2026-09-17 carry a
// `coverage` coordinate at all. Those older jobs are not migrated and never will be, because the
// suite is the record of what was actually run.
//
// So each shape below is a real first scenario taken verbatim from a job of that vintage, and the
// point of the file is that every one of them renders. A field added later must always be read as
// absent, never assumed.

const AUGUST = {
  name: "book_ride_saved_card_otp_noor",
  steps: 14,
  tests: "passes when the pickup and dropoff are confirmed and the saved card is charged",
  folder: "scenarios/book_ride_saved_card_otp_noor",
  use_case: "Book a ride for a recognized caller using a saved card after SMS verification.",
  sub_goals: ["addresses_confirmed", "payment_method_selected", "booking_confirmed"],
  instruction: "You are calling to book a ride to your office.",
};

const SEPTEMBER_11 = {
  ...AUGUST,
  folder: undefined,
  steps: undefined,
  scenario_id: "s-1",
  scenario_key: "book_ride_saved_card_otp_noor",
  branch: "saved card, recognized caller",
  call_direction: "inbound",
  answered_by: "agent",
  caller_awareness: "aware",
  background_noise: false,
  max_turns: 18,
  voicemail_style: "none",
  variables: { otp: "592804" },
  fixture: "rides",
  solution: "handlers/book_ride.py",
  persona: { name: "Noor", gender: "female", keywords: ["saved card"] },
};

const SEPTEMBER_17 = { ...SEPTEMBER_11, folder: "scenarios/x", steps: 14 };

const TODAY = {
  ...SEPTEMBER_17,
  coverage: { task: "book_ride_saved_card", counterparty: "recognized_regular", overlay: "none" },
};

const suite = (scenario) => ({
  id: "00000000-0000-0000-0000-000000000003",
  kind: "scenarios",
  title: "Generated scenarios",
  summary: "1 grounded scenario",
  data: [scenario],
});

describe.each([
  ["August, seven fields", AUGUST],
  ["September 11, no folder or steps", SEPTEMBER_11],
  ["September 17, no coverage", SEPTEMBER_17],
  ["today, every field", TODAY],
])("a suite from %s", (_when, scenario) => {
  it("renders its rows", () => {
    render(<StageOutput output={suite(scenario)} jobId="job-1" scenarios={[scenario]} />);
    expect(screen.getByText("book ride saved card otp noor")).toBeInTheDocument();
  });

  it("opens the edit panel", async () => {
    const user = userEvent.setup();
    render(<StageOutput output={suite(scenario)} jobId="job-1" scenarios={[scenario]} />);
    await user.click(screen.getByRole("button", { name: /edit scenario/i }));
    // The panel writes every section it can offer, and a field the scenario never had has to come
    // up blank rather than take the whole drawer down with it.
    expect(await screen.findByText("Directly editable")).toBeInTheDocument();
    expect(screen.getByDisplayValue(scenario.name)).toBeInTheDocument();
  });
});

describe("the coverage panel", () => {
  // Every job authored before 2026-09-17 has no coverage output at all, so the tab is the three
  // stages it always was. Nothing may appear in its place, and nothing may throw.
  it("is simply absent on a job that never produced one", () => {
    render(<StageOutput output={suite(AUGUST)} jobId="job-1" scenarios={[AUGUST]} />);
    expect(screen.queryByText(/Thinnest pairing/)).not.toBeInTheDocument();
    expect(screen.queryByText(/cross-tabulate/)).not.toBeInTheDocument();
  });

  // A job that produced the report but whose scenarios carry no coordinate is the in-between case:
  // a run that upgraded mid-flight, or a plan that declared no grid. It says so rather than
  // drawing an empty grid.
  it("says so when the report exists but no scenario was placed", () => {
    render(
      <StageOutput
        output={{
          id: "00000000-0000-0000-0000-000000000004",
          kind: "coverage",
          title: "Coverage",
          summary: "0 of 1 placed",
          data: { scenarios: 1, placed: 0, axes: {}, pairs: {} },
        }}
        jobId="job-1"
        scenarios={[SEPTEMBER_17]}
      />,
    );
    expect(screen.getByText(/nothing to cross-tabulate/i)).toBeInTheDocument();
  });
});
