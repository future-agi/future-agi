import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { mapCallDetail } from "src/api/simulate-environments/runDetail";
import ChatTranscriptPane from "../ChatTranscriptPane";

const theme = createTheme({
  palette: {
    orange: { o5: "#fff7ed", o10: "#ffedd5", o20: "#fed7aa", 500: "#f97316" },
  },
});

const detail = mapCallDetail({
  id: "chat-1",
  simulation_call_type: "text",
  transcript: [
    {
      speaker_role: "assistant",
      content: "Let me check that order.",
      start_time_seconds: 0,
      tool_calls: [
        {
          function: { name: "issue_refund", arguments: '{"order_id":"BK-1"}' },
        },
      ],
    },
  ],
  function_calls: [
    {
      name: "cancel_order",
      arguments: { order_id: "BK-2003" },
      result: { cancelled: false },
      duration_ms: 120,
      start_time_seconds: 2,
    },
  ],
});

const show = () =>
  render(
    <ThemeProvider theme={theme}>
      <ChatTranscriptPane turns={detail.turns} />
    </ThemeProvider>,
  );

describe("ChatTranscriptPane tool calls", () => {
  it("renders tool rows as themed cards instead of the heading text", async () => {
    const user = userEvent.setup();
    show();
    expect(screen.getByText("cancel_order · 120ms")).toBeInTheDocument();
    expect(screen.getByText("Tool result")).toBeInTheDocument();
    expect(screen.queryByText(/Function call/)).not.toBeInTheDocument();
    expect(screen.queryByText(/cancel_order\(/)).not.toBeInTheDocument();
    expect(
      screen.getByText(
        (_, element) =>
          element?.tagName === "P" && element.textContent === "0:02 · Tool",
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByText("Tool result"));
    expect(
      screen.getByText(
        (_, element) =>
          element?.tagName === "PRE" &&
          element.textContent === JSON.stringify({ cancelled: false }, null, 2),
      ),
    ).toBeInTheDocument();
  });

  it("renders a chat message's inline tool_calls as cards under its text", () => {
    show();
    expect(screen.getByText("Let me check that order.")).toBeInTheDocument();
    expect(screen.getByText("issue_refund")).toBeInTheDocument();
  });
});
