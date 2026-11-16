import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import AskRowsTable from "../AskRowsTable";
import AskRail from "../AskRail";
import { toRecord } from "../useAskRun";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../../useInstantEval", async (importOriginal) => ({
  ...(await importOriginal()),
  useInstantEvalHistory: () => ({
    runs: [
      {
        taskId: "t-users",
        name: "Instant eval · Did the agent resolve it?",
        status: "completed",
        createdAt: new Date().toISOString(),
        unit: "users",
        rows: 11502,
        sessions: 19480,
        items: [
          {
            configId: "q1",
            question: "Did the agent resolve it?",
            answerType: "yes_no",
            labels: ["Yes", "No"],
          },
        ],
        counts: { q1: { Yes: 7506, No: 3960, __error__: 36 } },
        answered: 11502,
        pending: 0,
      },
    ],
    isLoading: false,
    hasMore: false,
    loadMore: vi.fn(),
  }),
}));

const theme = createTheme({ palette: palette("light"), typography });
const QUESTION = {
  configId: "q1",
  question: "Did the agent resolve it?",
  answerType: "yes_no",
  labels: ["Yes", "No"],
};

const user = (id, answer, counts, confidence = 0.9) =>
  toRecord(
    {
      id,
      time: "2026-10-06T09:40:00+00:00",
      user_id: id,
      end_user_id: id,
      excerpt: "Where is my order?",
      sessions: Object.values(counts).reduce((s, v) => s + v, 0),
      facets: { model: "gpt-4.1", turns: 4, error: false },
      answers: {
        q1: { answer, counts, confidence, status: "completed" },
      },
    },
    "users",
  );

function renderTable(props = {}) {
  const onExport = vi.fn();
  render(
    <ThemeProvider theme={theme}>
      <AskRowsTable
        records={[
          user("cust_ana", "No", { No: 4, Yes: 1 }),
          user("cust_ben", "No", { No: 1 }),
          user("cust_fay", "__error__", { __error__: 2 }),
        ]}
        questions={[QUESTION]}
        unit="users"
        onOpen={vi.fn()}
        total={3}
        onExport={onExport}
        {...props}
      />
    </ThemeProvider>,
  );
  return { onExport };
}

describe("the rows table of a user question", () => {
  it("shows each user's answer with the sessions behind it", () => {
    renderTable();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("No· 4 of 5 sessions");
    expect(within(rows[0]).getByTitle("Sessions read: No 4 · Yes 1")).toBe(
      within(rows[0]).getByText("· 4 of 5 sessions"),
    );
    expect(rows[1]).toHaveTextContent("No· 1 of 1 session");
    // A user Jev couldn't answer has no answer to show sessions for.
    expect(rows[2]).toHaveTextContent("Failed");
    expect(rows[2]).not.toHaveTextContent("of 2 sessions");
  });

  it("shows a tie as low-confidence, and says it is a tie", () => {
    // cust_004's sessions split 2–2: the server scales the answer's
    // confidence by its share (1.0 × 2/4), as it does for any tie.
    renderTable({
      records: [user("cust_004", "Yes", { No: 2, Yes: 2 }, 0.5)],
      total: 1,
    });
    const [row] = screen.getAllByRole("row").slice(1);
    expect(row).toHaveTextContent("Yes· 2 of 4 sessions");
    expect(
      within(row).getByLabelText(
        "Tied across sessions (No 2 · Yes 2), so it counts as low-confidence",
      ),
    ).toBeInTheDocument();
    expect(within(row).queryByLabelText(/Jev was unsure/)).toBeNull();
  });

  it("exports the users, or their sessions", () => {
    const { onExport } = renderTable();
    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    expect(onExport).toHaveBeenLastCalledWith();
    fireEvent.click(screen.getByRole("button", { name: "Export sessions" }));
    expect(onExport).toHaveBeenLastCalledWith({ sessions: true });
  });

  it("offers no session export for other questions", () => {
    render(
      <ThemeProvider theme={theme}>
        <AskRowsTable
          records={[]}
          questions={[QUESTION]}
          unit="sessions"
          onOpen={vi.fn()}
          total={3}
          onExport={vi.fn()}
        />
      </ThemeProvider>,
    );
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Export sessions" })).toBe(
      null,
    );
  });
});

describe("the rows table when its rows fail to load", () => {
  it("says so and offers a retry instead of going blank", () => {
    const onRetry = vi.fn();
    render(
      <ThemeProvider theme={theme}>
        <AskRowsTable
          records={[]}
          questions={[QUESTION]}
          unit="sessions"
          onOpen={vi.fn()}
          total={0}
          loading={false}
          error
          onRetry={onRetry}
          onExport={vi.fn()}
        />
      </ThemeProvider>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "These sessions couldn't be loaded.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("History", () => {
  it("counts a user question's users and the sessions it read", () => {
    render(
      <ThemeProvider theme={theme}>
        <AskRail
          projectId="p1"
          configsById={{}}
          onToggle={vi.fn()}
          onNew={vi.fn()}
          onOpen={vi.fn()}
        />
      </ThemeProvider>,
    );
    expect(
      screen.getByText(/11,502 users · 19,480 sessions read/),
    ).toBeInTheDocument();
  });
});
