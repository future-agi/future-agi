import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import AskRowsTable from "../AskRowsTable";
import { toRecord } from "../useAskRun";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const theme = createTheme({ palette: palette("light"), typography });

// Five questions, as many as the composer asks in one pass, each with
// answers no other question gives.
const QUESTIONS = [
  {
    configId: "q1",
    question: "What did the customer come for?",
    answerType: "category",
    labels: ["Refund", "Billing", "Shipping"],
  },
  {
    configId: "q2",
    question: "How did the customer feel?",
    answerType: "category",
    labels: ["Calm", "Upset"],
  },
  {
    configId: "q3",
    question: "Which channel did they use?",
    answerType: "category",
    labels: ["Chat", "Email"],
  },
  {
    configId: "q4",
    question: "Did the customer ask for a human?",
    answerType: "category",
    labels: ["Asked for a human", "Stayed with the agent"],
  },
  {
    configId: "q5",
    question: "What language did they write in?",
    answerType: "category",
    labels: ["English", "Spanish"],
  },
];

const ANSWERS = {
  q1: "Refund",
  q2: "Upset",
  q3: "Email",
  q4: "Asked for a human",
  q5: "Spanish",
};

const session = toRecord(
  {
    id: "s1",
    time: "2026-10-06T09:40:00+00:00",
    user_id: "cust_ana",
    excerpt: "Where is my order?",
    facets: { model: "gpt-4.1", turns: 4, error: false },
    answers: Object.fromEntries(
      Object.entries(ANSWERS).map(([configId, answer]) => [
        configId,
        { answer, confidence: 0.95, status: "completed" },
      ]),
    ),
  },
  "sessions",
);

function renderTable(props = {}) {
  render(
    <ThemeProvider theme={theme}>
      <AskRowsTable
        records={[session]}
        questions={QUESTIONS}
        unit="sessions"
        onOpen={vi.fn()}
        total={1}
        onExport={vi.fn()}
        {...props}
      />
    </ThemeProvider>,
  );
}

describe("the rows table of a run with five questions", () => {
  it("shows every question's answer on each row", () => {
    renderTable();
    const [header, row] = screen.getAllByRole("row");
    expect(within(row).getByText("Refund")).toBeInTheDocument();
    expect(within(row).getByText("Upset")).toBeInTheDocument();
    expect(within(row).getByText("Email")).toBeInTheDocument();
    expect(within(row).getByText("Asked for a human")).toBeInTheDocument();
    expect(within(row).getByText("Spanish")).toBeInTheDocument();
    // When, User, Session, then a column per question.
    expect(within(header).getAllByRole("columnheader")).toHaveLength(8);
  });

  it("heads each answer column with its question, as the question chips name it", () => {
    renderTable();
    expect(
      screen.getByRole("columnheader", {
        name: "4 · Did the customer ask for a human?",
      }),
    ).toHaveAttribute("title", "Did the customer ask for a human?");
    expect(
      screen.getByRole("columnheader", {
        name: "5 · What language did they write in?",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: /^Answer/ })).toBeNull();
  });

  it("keeps a single Answer column for a run with one question", () => {
    renderTable({ questions: [QUESTIONS[0]] });
    expect(
      screen.getByRole("columnheader", { name: "Answer" }),
    ).toHaveAttribute("title", "What did the customer come for?");
    expect(screen.getAllByRole("columnheader")).toHaveLength(4);
  });
});
