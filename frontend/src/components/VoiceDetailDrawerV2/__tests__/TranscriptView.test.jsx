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

const plainCall = [
  { id: "1", speaker_role: "user", content: "One ride please", duration: 2 },
  { id: "2", speaker_role: "assistant", content: "Booked", duration: 1 },
];

const withSubActors = [
  ...plainCall,
  {
    id: "3",
    speaker_role: "sub_actor",
    speaker_name: "Rowan",
    actor_role: "roommate",
    channel: "audio_mix",
    content: "Make it two",
    duration: 1,
  },
  {
    id: "4",
    speaker_role: "sub_actor",
    speaker_name: "Sam",
    actor_role: "son",
    channel: "audio_handoff",
    content: "Hi, this is Sam",
    duration: 1,
  },
];

const chipLabels = () =>
  screen
    .getAllByRole("button")
    .map((b) => b.textContent)
    .filter((t) => ["All", "Assistant", "Customer", "Others"].includes(t));

describe("sub-actors in voice transcripts", () => {
  it("names each sub-actor with their role and how they were heard", () => {
    render(<TranscriptView transcript={withSubActors} />);
    expect(screen.getAllByText("Rowan · roommate").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Sam · son").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Heard nearby").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Takes the phone").length).toBeGreaterThan(0);
    expect(screen.queryByText(/not the customer/)).toBeNull();
    expect(screen.getByText(/^Others \d+%$/)).toBeInTheDocument();
  });

  it("lists who was on the call, customer first and the agent last", () => {
    render(<TranscriptView transcript={withSubActors} customerName="Maya" />);
    expect(screen.getByText("On the call:")).toBeInTheDocument();
    expect(screen.getByText("Maya · Customer")).toBeInTheDocument();
    // Each sub-actor shows once in the line and once on their turn.
    expect(screen.getAllByText("Rowan · roommate")).toHaveLength(2);
    expect(screen.getAllByText("Sam · son")).toHaveLength(2);
  });

  it("shows just a name, or Someone, for a call stored before roles and channels were kept", () => {
    render(
      <TranscriptView
        transcript={[
          ...plainCall,
          {
            id: "3",
            speaker_role: "sub_actor",
            speaker_name: "Rowan",
            content: "Two",
            duration: 1,
          },
          {
            id: "4",
            speaker_role: "sub_actor",
            content: "Hello?",
            duration: 1,
          },
        ]}
      />,
    );
    expect(screen.getAllByText("Rowan")).toHaveLength(2);
    expect(screen.getAllByText("Someone")).toHaveLength(2);
    // The chip and the line's customer, with no persona name to show.
    expect(screen.getAllByText("Customer")).toHaveLength(2);
    expect(screen.queryByText("Heard nearby")).toBeNull();
    expect(screen.queryByText(/undefined| ·$/)).toBeNull();
  });

  it("filters to the others on the call", async () => {
    render(<TranscriptView transcript={withSubActors} />);
    expect(chipLabels()).toEqual(["All", "Assistant", "Customer", "Others"]);
    await userEvent.click(screen.getByRole("button", { name: "Others" }));
    expect(screen.getByText("Make it two")).toBeInTheDocument();
    expect(screen.getByText("Hi, this is Sam")).toBeInTheDocument();
    expect(screen.queryByText("One ride please")).toBeNull();
    expect(screen.queryByText("Booked")).toBeNull();
  });

  it("renders a call with only the customer and the agent as before", () => {
    render(<TranscriptView transcript={plainCall} customerName="Maya" />);
    expect(chipLabels()).toEqual(["All", "Assistant", "Customer"]);
    expect(screen.queryByText("On the call:")).toBeNull();
    expect(screen.queryByText(/^Others/)).toBeNull();
    expect(screen.getByText(/^Customer \d+%$/)).toBeInTheDocument();
    expect(screen.getByText(/^Assistant \d+%$/)).toBeInTheDocument();
  });
});
