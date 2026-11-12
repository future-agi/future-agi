import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";

const fakes = vi.hoisted(() => ({ estimate: vi.fn() }));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../../useJevAvailability", async (importOriginal) => ({
  ...(await importOriginal()),
  useJevAvailability: () => ({ available: true, isLoading: false }),
}));
vi.mock("../../useInstantEval", async (importOriginal) => ({
  ...(await importOriginal()),
  estimateAsk: fakes.estimate,
  useInstantEvals: () => ({ all: [], items: [], isLoading: false }),
}));

import AskComposer from "../AskComposer";

const theme = createTheme({ palette: palette("light") });

// The past week, as a new question starts: the date window rides along with
// the filters as a created_at item, and is not a filter of its own.
const WINDOW = ["2026-11-03T00:00:00.000Z", "2026-11-10T00:00:00.000Z"];

// The server found nothing to read in this scope.
const emptyEstimate = (unit) => ({
  population: 0,
  population_is_lower_bound: false,
  rows_to_read: 0,
  questions: 1,
  credits_estimate: null,
  seconds_estimate: 10,
  needs_confirm: false,
  max_rows: 100000,
  confirm_rows: 5000,
  default_sample_size: null,
  sessions_per_user: unit === "users" ? 5 : null,
  credits_per_answer: null,
  choices: [
    {
      sample_size: null,
      rows_to_read: 0,
      seconds_estimate: 10,
      needs_confirm: false,
      allowed: false,
    },
  ],
});

const scopeFor = (unit, filters = []) => ({
  unit,
  filters,
  dateOption: "7D",
  window: WINDOW,
  label: filters.length ? "Past 7D · 1 filter" : "Past 7D",
});

function renderComposer(unit, scope) {
  fakes.estimate.mockResolvedValue(emptyEstimate(unit));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter>
          <AskComposer
            projectId="project-1"
            unit={unit}
            scope={scope}
            onScopeChange={() => {}}
            onAsk={() => {}}
          />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  fakes.estimate.mockReset();
});

describe("Ask on a project whose spans carry no session or user ids", () => {
  it("says sessions need session.id, with the docs, instead of removing a filter", async () => {
    renderComposer("sessions", scopeFor("sessions"));
    expect(
      await screen.findByText(
        "No sessions found in this date range. Sessions need session.id on your spans; if yours set it, widen the date range.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "How to set session.id" }),
    ).toHaveAttribute(
      "href",
      "https://docs.futureagi.com/docs/observe/features/session",
    );
    expect(screen.queryByText(/remove a filter/)).not.toBeInTheDocument();
  });

  it("says users need user.id, with the docs, instead of removing a filter", async () => {
    renderComposer("users", scopeFor("users"));
    expect(
      await screen.findByText(
        "No users found in this date range. Users need user.id on your spans; if yours set it, widen the date range.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "How to set user.id" }),
    ).toHaveAttribute(
      "href",
      "https://docs.futureagi.com/docs/observe/features/manual-tracing/set-session-user-id",
    );
    expect(screen.queryByText(/remove a filter/)).not.toBeInTheDocument();
  });

  it("asks only for a wider date range when no filter is set on traces", async () => {
    renderComposer("traces", scopeFor("traces"));
    expect(
      await screen.findByText(
        "No traces found in this date range. Widen the date range.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/remove a filter/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /How to set/ })).toBeNull();
  });

  it("still offers to remove a filter when one is set", async () => {
    renderComposer(
      "sessions",
      scopeFor("sessions", [
        {
          column_id: "total_cost",
          filter_config: {
            filter_type: "number",
            filter_op: "greater_than",
            filter_value: 1,
          },
        },
      ]),
    );
    expect(
      await screen.findByText(
        "No sessions match this date range and these filters. Widen the date range or remove a filter.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Sessions need session\.id/)).toBeNull();
    expect(screen.queryByRole("link", { name: /How to set/ })).toBeNull();
  });
});
