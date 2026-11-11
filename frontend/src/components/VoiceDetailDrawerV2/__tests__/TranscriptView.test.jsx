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

describe("function calls in voice transcripts", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("renders a separate header, arguments and result with recorded timing", () => {
    showTranscript();
    expect(screen.getByText("Function call")).toBeInTheDocument();
    expect(screen.getByText("geocode_address")).toBeInTheDocument();
    expect(screen.getByText("geocode_address").parentElement).toHaveTextContent(
      "Function call · geocode_address · 309ms",
    );
    const args = screen.getByText(`→ args: ${JSON.stringify(call.arguments)}`);
    const result = screen.getByText(`← result: ${JSON.stringify(call.result)}`);
    expect(args).not.toBe(result);
    expect(args.parentElement).toBe(result.parentElement);
    expect(screen.getByText("0:07")).toBeInTheDocument();
    expect(screen.getByText("Hello there")).toBeInTheDocument();
  });

  it("keeps structured function calls searchable and copyable", async () => {
    const user = userEvent.setup();
    showTranscript();
    await user.type(screen.getByPlaceholderText("Search transcript"), "SFO");
    expect(screen.queryByText("Hello there")).not.toBeInTheDocument();
    expect(screen.getByText("Function call")).toBeInTheDocument();
    expect(screen.getByText("SFO").tagName).toBe("MARK");
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
    expect(screen.getByText("← result: false")).toBeInTheDocument();
    expect(screen.getByText("← result: 0")).toBeInTheDocument();
    expect(screen.getByText("check").parentElement).toHaveTextContent(
      /^Function call · check$/,
    );
  });

  it("shows tool strings literally instead of interpreting them as markdown", () => {
    showTranscript([{ name: "read", result: "**raw** <script>text</script>" }]);
    expect(
      screen.getByText("← result: **raw** <script>text</script>"),
    ).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
  });
});
