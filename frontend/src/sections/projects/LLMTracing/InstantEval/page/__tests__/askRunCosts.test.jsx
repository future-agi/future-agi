/* eslint-disable react/prop-types */
import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthContext } from "src/auth/context/jwt/auth-context";
import { palette } from "src/theme/palette";

// What the Heatmap card was handed, each time it rendered.
const fakes = vi.hoisted(() => ({ heatmap: [] }));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureDenied: () => false,
}));
vi.mock("../../results/KeepAnswering", () => ({ default: () => null }));
vi.mock("../useAskRows", () => ({
  useAskRows: () => ({
    rows: [],
    total: 0,
    isLoading: false,
    hasMore: false,
    isFetchingMore: false,
    loadMore: () => {},
  }),
  fetchRowIds: vi.fn(async () => []),
  downloadRowsCsv: vi.fn(),
}));
vi.mock("../viz", async (importOriginal) => ({
  ...(await importOriginal()),
  AskTimeline: () => null,
  AskPatterns: () => null,
  AskHeatmap: (props) => {
    fakes.heatmap.push(props);
    return null;
  },
}));

import AskRunView from "../AskRunView";

const theme = createTheme({ palette: palette("light") });
const CONFIG = "config-1";
const QUESTION = {
  configId: CONFIG,
  question: "Was the issue resolved?",
  answerType: "yes_no",
  labels: ["Yes", "No"],
};
// A sample of 1,000 of a view's 5,800 sessions; all of it fits one run.
const SAMPLE = {
  population: 5800,
  population_is_lower_bound: false,
  requested: 1000,
  read: 1000,
  is_sample: true,
  can_extend: true,
  can_read_all: true,
  next_size: 1000,
};
// What one click of each control bills, as the server prices it.
const BILLED = {
  read_more: 60,
  read_all: 288,
  retry_failed: 0.3,
  run_again: 60,
};

// A finished sample, 5 of whose rows Jev couldn't answer.
const sampledRun = (actionCredits, { summary = {}, ...extra } = {}) => ({
  taskId: "task-1",
  status: "done",
  questions: [QUESTION],
  records: null,
  summary: {
    total: 1000,
    counts: { [CONFIG]: { Yes: 620, No: 375, __error__: 5 } },
    unsure: {},
    groups: {},
    sample: SAMPLE,
    action_credits: actionCredits,
    ...summary,
  },
  unit: "sessions",
  window: ["2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z"],
  total: 1000,
  answered: 995,
  failed: 5,
  pending: 0,
  notRead: 0,
  sample: SAMPLE,
  control: vi.fn().mockResolvedValue({}),
  refetch: vi.fn(),
  ...extra,
});

function renderRun(run) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <AuthContext.Provider
      value={{ authenticated: true, user: {}, role: "workspace_member" }}
    >
      <QueryClientProvider client={client}>
        <ThemeProvider theme={theme}>
          <MemoryRouter>
            <AskRunView
              projectId="project-1"
              run={run}
              unit={run.unit}
              onRunAgain={vi.fn()}
            />
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </AuthContext.Provider>,
  );
}

beforeEach(() => {
  fakes.heatmap = [];
});

describe("W10-INSTANT-EVAL-U02 a finished run's controls where answers are billed", () => {
  it("say what each click costs before it bills", async () => {
    const run = sampledRun(BILLED);
    renderRun(run);
    expect(
      screen.getByRole("button", {
        name: "Read 1,000 more · about 60 credits",
      }),
    ).toBeEnabled();
    const all = screen.getByRole("button", {
      name: "Read all 5,800 · about 288 credits",
    });
    expect(all).toBeEnabled();
    expect(
      screen.getAllByRole("button", {
        name: "Retry 5 rows · about 0.3 credits",
      }),
    ).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "Run again · about 60 credits" }),
    ).toBeEnabled();
    // The Heatmap's Read more reads the sample's size again.
    expect(fakes.heatmap.at(-1).readMoreCost).toBe("about 60 credits");

    // Still one click: the price is on the button, not a new step.
    await act(async () => {
      fireEvent.click(all);
    });
    expect(run.control).toHaveBeenCalledWith("read_more", {});
  });

  it("price the retry beside the estimate it would move", () => {
    // 107 of 1,107 unanswered: the retry sits with the estimate's bounds.
    renderRun(
      sampledRun(
        { ...BILLED, retry_failed: 6.42 },
        {
          total: 1107,
          answered: 1000,
          failed: 107,
          summary: {
            total: 1107,
            counts: { [CONFIG]: { Yes: 620, No: 380, __error__: 107 } },
          },
        },
      ),
    );
    expect(
      screen.getAllByRole("button", {
        name: "Retry 107 rows · about 6.4 credits",
      }),
    ).toHaveLength(1);
  });

  it("price the retry of a run that answered nothing", () => {
    renderRun(
      sampledRun(
        { read_more: null, read_all: null, retry_failed: 0.3, run_again: 0.3 },
        {
          answered: 0,
          failed: 5,
          total: 5,
          sample: null,
          summary: {
            total: 5,
            counts: { [CONFIG]: { __error__: 5 } },
            sample: null,
            failure: { message: "TypeSafe rejected the key.", every: true },
          },
        },
      ),
    );
    expect(
      screen.getAllByRole("button", {
        name: "Retry 5 rows · about 0.3 credits",
      }),
    ).toHaveLength(1);
  });

  it("name no credits where answers aren't billed", () => {
    renderRun(sampledRun(null));
    expect(
      screen.getByRole("button", { name: "Read 1,000 more" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Read all 5,800" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Retry 5 rows" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled();
    expect(fakes.heatmap.at(-1).readMoreCost ?? null).toBeNull();
    expect(screen.queryByText(/credits/)).toBeNull();
  });
});
