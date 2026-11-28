import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, userEvent } from "src/utils/test-utils";
import TranscriptView from "../TranscriptView";
import useVoiceAudioStore from "../voiceAudioStore";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/sections/common/CellMarkdown", () => ({
  default: ({ text }) => <div>{text}</div>,
}));

const transcript = [
  {
    id: "a",
    role: "user",
    content: "Is my order on its way?",
    start_time_seconds: 0.5,
    end_time_seconds: 1.7,
  },
  {
    id: "b",
    role: "assistant",
    content: "I'm checking your order.",
    start_time_seconds: 2.5,
    end_time_seconds: 4.2,
  },
  {
    id: "c",
    role: "assistant",
    content: "It shipped today.",
    start_time_seconds: 4.5,
    end_time_seconds: 6.2,
  },
];
const row = (extra) => ({
  key: "lookup",
  spanId: "span-lookup",
  name: "lookup_order",
  mcp: false,
  failed: false,
  startMs: 3000,
  endMs: 4000,
  interleaved: true,
  anchorTurnId: "c",
  ...extra,
});
// React and MUI generate ids per mount; everything else must match exactly.
const markup = (container) =>
  container.innerHTML
    .replace(/:r[0-9a-z]+:/g, ":id:")
    .replace(/mui-\d+/g, "mui-id");
const texts = () =>
  screen.getAllByText(/order|shipped|lookup_order/).map((el) => el.textContent);

describe("background activity in the transcript", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    useVoiceAudioStore.getState().reset?.();
  });

  it("renders the same with an empty activity list as with none (Voice Observability passes none)", () => {
    const without = markup(
      render(<TranscriptView transcript={transcript} />).container,
    );
    cleanup();
    const empty = markup(
      render(
        <TranscriptView
          transcript={transcript}
          activity={[]}
          onSelectActivity={vi.fn()}
        />,
      ).container,
    );
    expect(empty).toBe(without);
    expect(screen.queryAllByTestId("call-activity-row")).toHaveLength(0);
  });

  it("places a tool between the utterances around it, as a secondary row with its call time", () => {
    render(
      <TranscriptView
        transcript={transcript}
        activity={[row()]}
        onSelectActivity={vi.fn()}
      />,
    );
    expect(texts()).toEqual([
      "Is my order on its way?",
      "I'm checking your order.",
      "lookup_order",
      "It shipped today.",
    ]);
    const tool = screen.getByRole("button", {
      name: "View span lookup_order, tool call, from 0:03.0 to 0:04.0 after call start",
    });
    expect(tool).toHaveTextContent("Tool calllookup_order0:03.0–0:04.0");
  });

  it("puts trailing and ungroupable activity after the speech", () => {
    render(
      <TranscriptView
        transcript={transcript}
        activity={[
          row({
            key: "late",
            spanId: "s-late",
            name: "send_sms",
            anchorTurnId: null,
            startMs: 6500,
            endMs: null,
          }),
          row({
            key: "loose",
            spanId: "s-loose",
            name: "audit",
            interleaved: false,
            anchorTurnId: undefined,
            startMs: null,
            endMs: null,
          }),
        ]}
        onSelectActivity={vi.fn()}
      />,
    );
    const rows = screen.getAllByTestId("call-activity-row");
    expect(rows.map((el) => el.textContent)).toEqual([
      "Tool callsend_sms0:06.5",
      "Tool callauditTime not recorded",
    ]);
    expect(
      screen.getByRole("region", { name: "Background activity" }),
    ).toContainElement(rows[1]);
    expect(
      rows[0].compareDocumentPosition(screen.getByText("It shipped today.")),
    ).toBe(Node.DOCUMENT_POSITION_PRECEDING);
  });

  it("selects the span without seeking the recording", async () => {
    const user = userEvent.setup();
    const seekTo = vi.fn();
    useVoiceAudioStore.setState({ seekTo });
    const onSelectActivity = vi.fn();
    render(
      <TranscriptView
        transcript={transcript}
        activity={[row()]}
        onSelectActivity={onSelectActivity}
      />,
    );
    await user.click(screen.getByTestId("call-activity-row"));
    expect(onSelectActivity).toHaveBeenCalledExactlyOnceWith("span-lookup");
    expect(seekTo).not.toHaveBeenCalled();
    // A spoken turn still seeks to its own start, as in Voice Observability.
    await user.click(screen.getByText("It shipped today."));
    expect(seekTo).toHaveBeenCalledExactlyOnceWith(4.5);
  });

  it("keeps filters and search to speech", async () => {
    const user = userEvent.setup();
    render(
      <TranscriptView
        transcript={transcript}
        activity={[row()]}
        onSelectActivity={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Assistant" }));
    expect(screen.queryAllByTestId("call-activity-row")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "All" }));
    expect(screen.getAllByTestId("call-activity-row")).toHaveLength(1);
    await user.type(screen.getByPlaceholderText("Search transcript"), "lookup");
    expect(screen.queryAllByTestId("call-activity-row")).toHaveLength(0);
    expect(screen.getByText("No matching turns")).toBeInTheDocument();
  });

  it("keeps a row whose turn the list does not show, in the separate group", () => {
    render(
      <TranscriptView
        transcript={transcript}
        activity={[row({ anchorTurnId: "gone" })]}
        onSelectActivity={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("region", { name: "Background activity" }),
    ).toContainElement(screen.getByTestId("call-activity-row"));
  });

  it("says when a span began before the call it belongs to", () => {
    render(
      <TranscriptView
        transcript={transcript}
        activity={[
          row({
            interleaved: false,
            anchorTurnId: undefined,
            startMs: -2000,
            endMs: -1000,
          }),
          row({
            key: "x",
            spanId: "s-x",
            name: "warmup",
            interleaved: false,
            anchorTurnId: undefined,
            startMs: -500,
            endMs: 500,
          }),
        ]}
        onSelectActivity={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", {
        name: "View span lookup_order, tool call, from 0:02.0 before call start to 0:01.0 before call start",
      }),
    ).toHaveTextContent("Tool calllookup_order−0:02.0–−0:01.0");
    expect(
      screen.getByRole("button", {
        name: "View span warmup, tool call, from 0:00.5 before call start to 0:00.5 after call start",
      }),
    ).toHaveTextContent("Tool callwarmup−0:00.5–0:00.5");
  });

  it("labels MCP and failure only when the row says so", () => {
    render(
      <TranscriptView
        transcript={transcript}
        activity={[row({ mcp: true, failed: true })]}
        onSelectActivity={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", {
        name: "View span lookup_order, mcp tool, failed, from 0:03.0 to 0:04.0 after call start",
      }),
    ).toHaveTextContent("MCP toollookup_orderFailed0:03.0–0:04.0");
  });
});
