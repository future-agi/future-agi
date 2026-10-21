import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { functionCallTranscriptRows } from "src/api/simulate-environments/runDetail";
import TranscriptView from "../TranscriptView";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/sections/common/CellMarkdown", () => ({
  default: ({ text }) => <div>{text}</div>,
}));

const call = {
  id: "lookup-1",
  name: "geocode_address",
  arguments: { query: "SFO Airport", market: "US-SF" },
  result: { candidates: [{ city: "San Francisco", confidence: 0.8 }] },
  duration_ms: 309,
  start_time_seconds: 7,
  end_time_seconds: 7.309,
};

const showTranscript = (calls = [call]) => {
  const transcript = [
    { id: "speech-1", speaker_role: "user", content: "Hello there" },
    ...functionCallTranscriptRows(calls),
  ];
  return render(<TranscriptView transcript={transcript} />);
};

const preWithText = (text) =>
  screen.getByText(
    (_, element) => element?.tagName === "PRE" && element.textContent === text,
  );

describe("function calls in voice transcripts", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("renders each call and its result as themed cards with recorded timing", async () => {
    const user = userEvent.setup();
    showTranscript();
    expect(screen.getByText("geocode_address · 309ms")).toBeInTheDocument();
    expect(screen.getByText("Tool result")).toBeInTheDocument();
    expect(screen.queryByText(/Function call/)).not.toBeInTheDocument();
    await user.click(screen.getByText("geocode_address · 309ms"));
    expect(
      preWithText(JSON.stringify(call.arguments, null, 2)),
    ).toBeInTheDocument();
    expect(
      preWithText(JSON.stringify(call.result, null, 2)),
    ).toBeInTheDocument();
    expect(screen.getByText("0:07")).toBeInTheDocument();
    expect(screen.getByText("Hello there")).toBeInTheDocument();
  });

  it("keeps structured function calls searchable and copyable", async () => {
    const user = userEvent.setup();
    showTranscript();
    await user.type(screen.getByPlaceholderText("Search transcript"), "SFO");
    expect(screen.queryByText("Hello there")).not.toBeInTheDocument();
    expect(screen.getByText("geocode_address · 309ms")).toBeInTheDocument();
    const copy = vi.spyOn(navigator.clipboard, "writeText");
    await user.click(
      screen.getByRole("button", { name: "Copy function call" }),
    );
    expect(copy).toHaveBeenCalledWith(
      expect.stringContaining(functionCallTranscriptRows([call])[0].content),
    );
  });

  it("preserves false/zero results and omits unavailable timing", () => {
    showTranscript([
      { id: "a", name: "check", arguments: {}, result: false },
      { id: "b", function: { name: "count", arguments: "{}" }, output: 0 },
    ]);
    expect(screen.getAllByText("Tool result")).toHaveLength(2);
    expect(screen.getByText("false")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getByText("check")).toBeInTheDocument();
    expect(screen.getByText("count")).toBeInTheDocument();
  });

  it("shows a call without a result as a single card", () => {
    showTranscript([{ id: "c", name: "pending_lookup", arguments: {} }]);
    expect(screen.getByText("pending_lookup")).toBeInTheDocument();
    expect(screen.queryByText("Tool result")).not.toBeInTheDocument();
  });

  it("shows tool strings literally instead of interpreting them as markdown", () => {
    showTranscript([{ name: "read", result: "**raw** <script>text</script>" }]);
    expect(
      screen.getByText("**raw** <script>text</script>"),
    ).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
  });
});
