import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";

const history = vi.hoisted(() => ({ runs: [] }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../../useInstantEval", async (importOriginal) => ({
  ...(await importOriginal()),
  useInstantEvalHistory: () => ({
    runs: history.runs,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
}));
import AskRail from "../AskRail";

const theme = createTheme({ palette: palette("light") });
const failedRun = (items) => ({
  taskId: "failed-task",
  name: "Instant eval · Resolved?",
  status: "failed",
  unit: "sessions",
  rows: 3,
  createdAt: "2026-10-27T00:00:00Z",
  answered: 3,
  pending: 0,
  items,
  counts: { q1: { __error__: 3 } },
});
const QUESTION = {
  configId: "q1",
  templateId: "evaluator-1",
  question: "Resolved?",
  labels: ["Yes", "No"],
  answerType: "yes_no",
};

function renderRail() {
  const onOpen = vi.fn();
  const onRecreate = vi.fn();
  render(
    <MemoryRouter>
      <ThemeProvider theme={theme}>
        <AskRail
          projectId="p1"
          configsById={{}}
          onToggle={vi.fn()}
          onNew={vi.fn()}
          onOpen={onOpen}
          onRecreate={onRecreate}
        />
      </ThemeProvider>
    </MemoryRouter>,
  );
  return { onOpen, onRecreate };
}

beforeEach(() => {
  history.runs = [];
});

describe("a failed run's card in History", () => {
  it("keeps its fixes outside the link that opens the run", () => {
    history.runs = [failedRun([QUESTION])];
    const { onOpen } = renderRail();
    const card = screen.getByRole("link", { name: "Open the run: Resolved?" });
    // No control inside the open-run link.
    expect(
      within(card).queryByRole("link", { name: "Fix evaluator" }),
    ).toBeNull();
    expect(within(card).queryAllByRole("button")).toHaveLength(0);
    // The fix is its own control, beside the link, and doesn't open the run.
    const fix = screen.getByRole("link", { name: "Fix evaluator" });
    expect(card).not.toContainElement(fix);
    expect(fix).toHaveAttribute("href", "/dashboard/evaluations/evaluator-1");
    fireEvent.click(fix);
    expect(onOpen).not.toHaveBeenCalled();
    // The link says the run failed, so the fix after it has its reason.
    expect(card).toHaveAccessibleDescription(/Failed before answering/);
    // The card still opens the run, once, by click or Enter.
    fireEvent.click(card);
    expect(onOpen).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(card, { key: "Enter" });
    expect(onOpen).toHaveBeenCalledTimes(2);
  });

  it("keeps Recreate question outside the link too", () => {
    const { templateId: _gone, ...deleted } = QUESTION;
    const run = failedRun([deleted]);
    history.runs = [run];
    const { onOpen, onRecreate } = renderRail();
    const card = screen.getByRole("link", { name: "Open the run: Resolved?" });
    expect(
      within(card).queryByRole("button", { name: "Recreate question" }),
    ).toBeNull();
    const recreate = screen.getByRole("button", { name: "Recreate question" });
    expect(card).not.toContainElement(recreate);
    fireEvent.click(recreate);
    expect(onRecreate).toHaveBeenCalledExactlyOnceWith(run);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("is a plain link for a run that didn't fail", () => {
    history.runs = [
      {
        ...failedRun([QUESTION]),
        status: "completed",
        counts: { q1: { Yes: 3 } },
      },
    ];
    renderRail();
    const card = screen.getByRole("link", { name: "Open the run: Resolved?" });
    expect(card).not.toHaveAttribute("aria-describedby");
    expect(screen.queryByRole("link", { name: "Fix evaluator" })).toBeNull();
  });
});
