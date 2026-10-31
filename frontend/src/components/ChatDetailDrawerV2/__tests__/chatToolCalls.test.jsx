import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import ChatTranscriptView from "../ChatTranscriptView";
import { getChatToolCalls } from "../chatTranscriptUtils";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/sections/common/CellMarkdown", () => ({
  default: ({ text }) => <div>{text}</div>,
}));

const assistantTurn = {
  id: "turn-2",
  speaker_role: "assistant",
  created_at: "2026-10-14T10:00:05Z",
  messages: ["Let me check that booking."],
  content: [
    {
      role: "assistant",
      content: "Let me check that booking.",
      tool_calls: [
        {
          id: "call_1",
          type: "function",
          function: {
            name: "get_booking",
            arguments: '{"booking_id":"HB-5001"}',
          },
        },
        {
          id: "call_2",
          type: "function",
          function: { name: "find_hotels", arguments: '{"city":"Lisbon"}' },
        },
      ],
    },
    { role: "tool", tool_call_id: "call_1", content: '{"status":"confirmed"}' },
  ],
};

describe("getChatToolCalls", () => {
  it("pairs each tool call with its result by tool_call_id", () => {
    expect(getChatToolCalls(assistantTurn)).toEqual([
      {
        id: "call_1",
        name: "get_booking",
        arguments: '{"booking_id":"HB-5001"}',
        result: '{"status":"confirmed"}',
      },
      { id: "call_2", name: "find_hotels", arguments: '{"city":"Lisbon"}' },
    ]);
  });

  it("falls back to the turn's own tool_calls when the messages carry none", () => {
    const turn = {
      content: [{ role: "assistant", content: "hi" }],
      tool_calls: [{ id: "x", function: { name: "lookup", arguments: "{}" } }],
    };
    expect(getChatToolCalls(turn)).toEqual([
      { id: "x", name: "lookup", arguments: "{}" },
    ]);
  });

  it("answers nothing for a turn without raw messages or tool calls", () => {
    expect(getChatToolCalls({ messages: ["hello"] })).toEqual([]);
    expect(getChatToolCalls(undefined)).toEqual([]);
  });
});

describe("chat transcript tool calls", () => {
  it("shows each call and its result as themed cards under the turn", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    const user = userEvent.setup();
    render(
      <ChatTranscriptView
        data={{
          status: "completed",
          simulation_call_type: "text",
          transcript: [
            {
              id: "turn-1",
              speaker_role: "user",
              created_at: "2026-10-14T10:00:00Z",
              messages: ["Check HB-5001"],
              content: [{ role: "user", content: "Check HB-5001" }],
            },
            assistantTurn,
          ],
        }}
      />,
    );

    expect(screen.getByText("Let me check that booking.")).toBeInTheDocument();
    expect(screen.getByText("get_booking")).toBeInTheDocument();
    expect(screen.getByText("find_hotels")).toBeInTheDocument();
    expect(screen.getAllByText("Tool result")).toHaveLength(1);

    await user.click(screen.getByText("get_booking"));
    expect(screen.getByText('{"booking_id":"HB-5001"}')).toBeInTheDocument();
  });
});
