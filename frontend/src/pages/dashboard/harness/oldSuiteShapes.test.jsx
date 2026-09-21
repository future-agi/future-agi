import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

import StageOutput from "./StageOutput";

vi.mock("src/api/harness/harness", () => ({
  amendHarnessScenarios: vi.fn(),
}));

// The scenario document has grown over time and older suites are never migrated, so every shape
// still has to render. A field added later is read as absent, never assumed.

const SEVEN_FIELDS = {
  name: "book_ride_saved_card_otp_noor",
  steps: 14,
  tests: "passes when the pickup and dropoff are confirmed and the saved card is charged",
  folder: "scenarios/book_ride_saved_card_otp_noor",
  use_case: "Book a ride for a recognized caller using a saved card after SMS verification.",
  sub_goals: ["addresses_confirmed", "payment_method_selected", "booking_confirmed"],
  instruction: "You are calling to book a ride to your office.",
};

const WITH_PERSONA = {
  ...SEVEN_FIELDS,
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

const NO_COVERAGE = { ...WITH_PERSONA, folder: "scenarios/x", steps: 14 };

const EVERY_FIELD = {
  ...NO_COVERAGE,
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
  ["seven fields", SEVEN_FIELDS],
  ["no folder or steps", WITH_PERSONA],
  ["no coverage", NO_COVERAGE],
  ["every field", EVERY_FIELD],
])("a suite from %s", (_when, scenario) => {
  it("renders its rows", () => {
    render(<StageOutput output={suite(scenario)} jobId="job-1" scenarios={[scenario]} />);
    expect(screen.getByText("book ride saved card otp noor")).toBeInTheDocument();
  });

  it("opens the edit panel", async () => {
    const user = userEvent.setup();
    render(<StageOutput output={suite(scenario)} jobId="job-1" scenarios={[scenario]} />);
    await user.click(screen.getByRole("button", { name: /edit scenario/i }));
    expect(await screen.findByText("Directly editable")).toBeInTheDocument();
    expect(screen.getByDisplayValue(scenario.name)).toBeInTheDocument();
  });
});

describe("the coverage panel", () => {
  it("is simply absent on a job that never produced one", () => {
    render(<StageOutput output={suite(SEVEN_FIELDS)} jobId="job-1" scenarios={[SEVEN_FIELDS]} />);
    expect(screen.queryByText(/Thinnest pairing/)).not.toBeInTheDocument();
    expect(screen.queryByText(/cross-tabulate/)).not.toBeInTheDocument();
  });

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
        scenarios={[NO_COVERAGE]}
      />,
    );
    expect(screen.getByText(/nothing to cross-tabulate/i)).toBeInTheDocument();
  });
});
