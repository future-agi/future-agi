import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";

import StageOutput from "./StageOutput";

const mocks = vi.hoisted(() => ({ listHarnessScenarios: vi.fn() }));

vi.mock("src/api/harness/harness", () => ({
  amendHarnessScenarios: vi.fn(),
  listHarnessScenarios: mocks.listHarnessScenarios,
}));

// The suite arrives from its own endpoint, so an old shape is served rather than passed in.
const served = (scenario) => ({
  count: 1,
  total_pages: 1,
  results: [scenario],
  fields: [],
  scenario_editing: {
    editable_fields: ["tests", "max_turns", "background_noise"],
    persona_fields: ["accent", "languages", "personality"],
  },
  coverage: null,
});

const show = (ui) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      {ui}
    </QueryClientProvider>,
  );

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
  it("renders its rows", async () => {
    mocks.listHarnessScenarios.mockResolvedValue(served(scenario));
    show(<StageOutput output={suite(scenario)} jobId="job-1" scenarios={[scenario]} />);
    expect(
      await screen.findByText("book ride saved card otp noor"),
    ).toBeInTheDocument();
  });

  it("opens the edit panel", async () => {
    const user = userEvent.setup();
    mocks.listHarnessScenarios.mockResolvedValue(served(scenario));
    show(<StageOutput output={suite(scenario)} jobId="job-1" scenarios={[scenario]} />);
    await screen.findByText("book ride saved card otp noor");
    await user.click(screen.getByRole("button", { name: /edit scenario/i }));
    expect(await screen.findByText("Directly editable")).toBeInTheDocument();
    expect(screen.getByDisplayValue(scenario.name)).toBeInTheDocument();
  });
});

describe("the coverage panel", () => {
  it("is simply absent on a job that never produced one", () => {
    mocks.listHarnessScenarios.mockResolvedValue(served(SEVEN_FIELDS));
    show(<StageOutput output={suite(SEVEN_FIELDS)} jobId="job-1" scenarios={[SEVEN_FIELDS]} />);
    expect(screen.queryByText(/Thinnest pairing/)).not.toBeInTheDocument();
    expect(screen.queryByText(/cross-tabulate/)).not.toBeInTheDocument();
  });

  it("no longer draws a grid of its own, because the suite serves one", () => {
    mocks.listHarnessScenarios.mockResolvedValue(served(NO_COVERAGE));
    show(
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
    // The suite's own grid is cross-tabulated in SQL over every scenario. A second grid drawn
    // here from whatever JSON reached the browser showed the same thing twice, from two sources.
    expect(screen.queryByText(/cross-tabulate/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Thinnest pairing/)).not.toBeInTheDocument();
  });
});
