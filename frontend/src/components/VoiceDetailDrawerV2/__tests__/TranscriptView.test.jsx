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
    expect(screen.getByText(/^Others \d+%$/)).toBeInTheDocument();
  });

  it("names a sub-actor, not their raw role, on the speaker timeline", () => {
    render(<TranscriptView transcript={withSubActors} />);
    expect(
      screen.getByLabelText(/^Rowan · roommate · \d+:\d{2}$/),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(/^sub_actor/)).toBeNull();
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

  it("shows every turn when Others was picked and the next call has none", async () => {
    const { rerender } = render(<TranscriptView transcript={withSubActors} />);
    await userEvent.click(screen.getByRole("button", { name: "Others" }));
    rerender(<TranscriptView transcript={plainCall} />);
    expect(screen.queryByText("No matching turns")).toBeNull();
    expect(screen.getByText("One ride please")).toBeInTheDocument();
    expect(screen.getByText("Booked")).toBeInTheDocument();
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

const customer = {
  id: "1",
  speaker_role: "user",
  content: "Can I get a discount?",
  start_time_ms: 1000,
};

const showActions = (actions) =>
  render(
    <TranscriptView
      customerName="Maya"
      transcript={[customer, ...functionCallTranscriptRows(actions)]}
    />,
  );

describe("world actors in voice transcripts", () => {
  it("shows a world actor's action as an event, with its raw record folded behind Details", async () => {
    showActions([
      {
        id: "approve-1",
        name: "crm.approve_discount",
        arguments: { percent: 20 },
        result: { approved: false },
        actor_id: "manager",
        actor_name: "Arjun Mehta",
        actor_role: "store manager",
        outcome: "refused",
        start_time_ms: 2000,
      },
    ]);

    expect(screen.getByText("Arjun Mehta · store manager")).toBeInTheDocument();
    expect(
      screen.getByText("Refused · crm.approve_discount"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Function call · crm.approve_discount/),
    ).toBeNull();
    expect(screen.queryByText(/args:/)).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /^Details/ }));
    expect(screen.getByText(/→ args: .*"percent":20/)).toBeInTheDocument();
    expect(
      screen.getByText(/← result: .*"approved":false/),
    ).toBeInTheDocument();
  });

  it("names how each action went: committed, refused or failed", () => {
    showActions(
      [
        ["committed", "crm.note"],
        ["refused", "crm.refund"],
        ["faulted", "crm.cancel"],
      ].map(([outcome, name], i) => ({
        id: `a-${i}`,
        name,
        outcome,
        error: outcome === "faulted" ? "timeout" : undefined,
        actor_id: "manager",
        actor_name: "Arjun Mehta",
        start_time_ms: 2000 + i,
      })),
    );
    expect(screen.getByText("Committed · crm.note")).toBeInTheDocument();
    expect(screen.getByText("Refused · crm.refund")).toBeInTheDocument();
    expect(screen.getByText("Failed · crm.cancel")).toBeInTheDocument();
  });

  it("shows just the name and the action for a row stored without a role or an outcome", () => {
    showActions([
      {
        id: "a",
        name: "crm.lookup",
        actor_id: "manager",
        actor_name: "Arjun Mehta",
        start_time_ms: 2000,
      },
      {
        id: "b",
        name: "crm.hold",
        actor_id: "clerk",
        actor_name: "",
        start_time_ms: 3000,
      },
    ]);
    expect(screen.getByText("Arjun Mehta")).toBeInTheDocument();
    expect(screen.getByText("clerk")).toBeInTheDocument();
    expect(screen.getByText("crm.lookup")).toBeInTheDocument();
    expect(screen.queryByText(/undefined|^Action| ·$/)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Details/ })).toBeNull();
  });

  it("keeps world actors off the line of who was on the call, and findable under Others", async () => {
    showActions([
      {
        id: "a",
        name: "crm.lookup",
        actor_id: "manager",
        actor_name: "Arjun Mehta",
        actor_role: "store manager",
        outcome: "committed",
        start_time_ms: 2000,
      },
    ]);
    expect(screen.queryByText("On the call:")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Others" }));
    expect(screen.getByText("Committed · crm.lookup")).toBeInTheDocument();
    expect(screen.queryByText("Can I get a discount?")).toBeNull();
  });

  it("opens the fold when a search matched inside it, so the row shows why it stayed", async () => {
    const user = userEvent.setup();
    showActions([
      {
        id: "a",
        name: "crm.approve",
        arguments: { percent: 20 },
        actor_id: "manager",
        actor_name: "Arjun Mehta",
        start_time_ms: 2000,
      },
    ]);
    await user.type(
      screen.getByPlaceholderText("Search transcript"),
      "percent",
    );
    expect(screen.getByText("percent").tagName).toBe("MARK");
  });
});
