import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";

const http = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: http.get },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureDenied: () => false,
}));
vi.mock("../../results/KeepAnswering", () => ({ default: () => null }));

import AskRunView from "../AskRunView";

const theme = createTheme({ palette: palette("light") });
const QUESTIONS = [
  {
    configId: "config-1",
    question: "Was the issue resolved?",
    answerType: "yes_no",
    labels: ["Yes", "No"],
  },
  {
    configId: "config-2",
    question: "Did the customer leave satisfied with the answer they got?",
    answerType: "yes_no",
    labels: ["Yes", "No"],
  },
];

// A finished run over 53 sessions, 2 of whose rows Jev couldn't answer.
const finishedRun = () => ({
  taskId: "task-1",
  status: "done",
  questions: QUESTIONS,
  records: null,
  summary: {
    total: 53,
    counts: {
      "config-1": { Yes: 48, No: 3, __error__: 2 },
      "config-2": { Yes: 40, No: 11, __error__: 2 },
    },
    unsure: {},
    groups: {},
  },
  unit: "sessions",
  tz: "UTC",
  window: ["2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z"],
  total: 53,
  answered: 51,
  failed: 2,
  pending: 0,
  notRead: 0,
  sample: null,
  control: vi.fn().mockResolvedValue({}),
  refetch: vi.fn(),
});

function renderRun(run) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter>
          <AskRunView projectId="project-1" run={run} unit={run.unit} />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  http.get.mockReset();
  http.get.mockResolvedValue({
    data: { result: { rows: [], total: 0, next_cursor: null } },
  });
});

describe("W10-INSTANT-EVAL-U08 the run page at laptop widths", () => {
  it("wraps the status line, so the failed-rows note is never cut off", async () => {
    renderRun(finishedRun());
    const note = screen.getByText(/2 rows couldn't be fully answered/);
    const status = note.closest(".MuiTypography-root");
    expect(status).toHaveTextContent(/^51 of 53 sessions answered/);
    expect(status).not.toHaveClass("MuiTypography-noWrap");
    expect(getComputedStyle(status).whiteSpace).not.toBe("nowrap");
    await waitFor(() => expect(http.get).toHaveBeenCalled());
  });

  it("lets a chart card's controls drop below its title, and the Colour by select shrink to the card", async () => {
    renderRun(finishedRun());
    const card = screen.getByRole("region", { name: "Timeline of answers" });
    const header = card.firstElementChild;
    expect(getComputedStyle(header).flexWrap).toBe("wrap");
    const colourBy = within(card)
      .getByRole("combobox", { name: /^Colour by/ })
      .closest(".MuiTextField-root");
    expect(colourBy).toHaveStyle({ maxWidth: "100%" });
    // The card's controls wrap among themselves, inside the card.
    const actions = colourBy.parentElement;
    expect(getComputedStyle(actions).flexWrap).toBe("wrap");
    expect(actions).toHaveStyle({ maxWidth: "100%" });
    await waitFor(() => expect(http.get).toHaveBeenCalled());
  });
});
