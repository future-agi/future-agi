/* eslint-disable react/prop-types */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthContext } from "src/auth/context/jwt/auth-context";
import { palette } from "src/theme/palette";
import { VIEWER_CANT_RUN } from "src/sections/projects/runPermission";

const fakes = vi.hoisted(() => ({ estimate: vi.fn() }));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureDenied: () => false,
}));
vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: () => null,
}));
vi.mock("../../results/KeepAnswering", () => ({ default: () => null }));
vi.mock("../../useJevAvailability", () => ({
  useJevAvailability: () => ({ available: true, isLoading: false }),
}));
vi.mock("../../useInstantEval", async (importOriginal) => ({
  ...(await importOriginal()),
  estimateAsk: fakes.estimate,
  useInstantEvals: () => ({ all: [], items: [], isLoading: false }),
}));
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
  AskHeatmap: () => null,
}));

import AskComposer from "../AskComposer";
import AskRunView from "../AskRunView";

const theme = createTheme({ palette: palette("light") });
const CONFIG = "config-1";
const ESTIMATE = {
  population: 4,
  population_is_lower_bound: false,
  rows_to_read: 4,
  questions: 1,
  credits_estimate: null,
  seconds_estimate: 10,
  needs_confirm: false,
  max_rows: 100000,
  confirm_rows: 5000,
  default_sample_size: null,
  sessions_per_user: null,
  credits_per_answer: null,
  choices: [
    {
      sample_size: null,
      rows_to_read: 4,
      seconds_estimate: 10,
      needs_confirm: false,
      allowed: true,
    },
  ],
};
const runningRun = {
  taskId: "task-1",
  status: "running",
  questions: [
    {
      configId: CONFIG,
      question: "Was the issue resolved?",
      answerType: "yes_no",
      labels: ["Yes", "No"],
    },
  ],
  records: null,
  summary: {
    total: 10,
    counts: { [CONFIG]: { Yes: 2, No: 1 } },
    unsure: {},
    groups: {},
  },
  unit: "traces",
  window: ["2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z"],
  total: 10,
  answered: 3,
  failed: 0,
  pending: 7,
  notRead: 0,
  sample: null,
  control: vi.fn(),
  refetch: vi.fn(),
};

// Signed in with this workspace role, as the auth provider would hold it.
function renderAs(role, ui) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <AuthContext.Provider value={{ authenticated: true, user: {}, role }}>
      <QueryClientProvider client={client}>
        <ThemeProvider theme={theme}>
          <MemoryRouter>{ui}</MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </AuthContext.Provider>,
  );
}

const composer = (
  <AskComposer
    projectId="project-1"
    unit="sessions"
    scope={{ filters: [], dateOption: "7D" }}
    onScopeChange={() => {}}
    onAsk={() => {}}
  />
);

beforeEach(() => {
  fakes.estimate.mockReset();
  fakes.estimate.mockResolvedValue(ESTIMATE);
});

describe("a Viewer on the Ask page", () => {
  it("sees what a question would read, and can't ask it", async () => {
    renderAs("workspace_viewer", composer);
    const ask = await screen.findByRole("button", {
      name: "Ask Jev about 4 sessions",
    });
    expect(ask).toBeDisabled();
    expect(screen.getByText(VIEWER_CANT_RUN)).toBeInTheDocument();
  });

  it("is offered the question by a Member's role", async () => {
    renderAs("workspace_member", composer);
    const ask = await screen.findByRole("button", {
      name: "Ask Jev about 4 sessions",
    });
    await waitFor(() => expect(ask).toBeEnabled());
    expect(screen.queryByText(VIEWER_CANT_RUN)).not.toBeInTheDocument();
  });

  it("reads a live run without pausing or cancelling it", () => {
    renderAs(
      "Viewer",
      <AskRunView projectId="project-1" run={runningRun} unit="traces" />,
    );
    expect(screen.getByRole("button", { name: "Pause" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByText(VIEWER_CANT_RUN)).toBeInTheDocument();
  });

  it("can't retry the rows of a run that answered nothing", () => {
    // Run ee2c89df: the alert's Retry stayed enabled for a Viewer and the
    // server refused it with a bare "That didn't work."
    const allFailed = {
      ...runningRun,
      status: "done",
      answered: 0,
      failed: 5,
      pending: 0,
      total: 5,
      summary: {
        total: 5,
        counts: { [CONFIG]: { __error__: 5 } },
        unsure: {},
        groups: {},
        failure: { message: "TypeSafe rejected the key.", every: true },
      },
    };
    renderAs(
      "workspace_viewer",
      <AskRunView projectId="project-1" run={allFailed} unit="traces" />,
    );
    expect(screen.getByRole("button", { name: "Retry 5 rows" })).toBeDisabled();
    expect(screen.getByText(VIEWER_CANT_RUN)).toBeInTheDocument();
  });

  it("leaves a Member the run's controls", () => {
    renderAs(
      "workspace_member",
      <AskRunView projectId="project-1" run={runningRun} unit="traces" />,
    );
    expect(screen.getByRole("button", { name: "Pause" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
    expect(screen.queryByText(VIEWER_CANT_RUN)).not.toBeInTheDocument();
  });
});
