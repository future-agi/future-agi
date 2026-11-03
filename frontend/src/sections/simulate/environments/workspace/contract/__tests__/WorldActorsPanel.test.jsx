import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const mutate = vi.fn();
let actorsData;

vi.mock("src/api/simulate-environments/versions", () => ({
  useActors: () => ({ data: actorsData }),
  useActorView: () => ({
    data: {
      sees: [
        { input: "actor.goal", value: "Approve fast" },
        {
          input: "actor.knowledge",
          value: [{ kind: "facts", text: "The limit is 25%" }],
        },
        {
          input: "actor.behaviour",
          value: { response_timing: { kind: "delayed", seconds: 5 } },
        },
      ],
      never_sees: [
        "scenario.solution",
        "scenario.sub_goals",
        "file.sub_goals.json",
      ],
    },
  }),
  useEditActor: () => ({
    mutate,
    reset: vi.fn(),
    isPending: false,
    error: null,
  }),
}));

const { default: WorldActorsPanel } = await import("../WorldActorsPanel");

const MANAGER = {
  id: "manager",
  name: "Priya Raman",
  role: "sales manager",
  goal: "Approve only discounts she can justify",
  permitted_actions: ["crm.approve_discount"],
};

const ACTIONS = [
  {
    action: "crm.approve_discount",
    tool: "approve_discount",
    description: "Approve a pending discount",
  },
  {
    action: "crm.request_approval",
    tool: "request_approval",
    description: "Ask a manager to approve",
  },
];
const CHANNELS = {
  audio_mix: "a voice near the person",
  audio_handoff: "takes the phone",
  ivr: "a phone menu",
};
const cast = (actors, extra = {}) => ({
  actors,
  source: "draft",
  revision: 3,
  actions: ACTIONS,
  channels: CHANNELS,
  loudness: { channels: ["audio_mix"], automatic: [0.333, 0.667] },
  ...extra,
});
// Open an MUI select by its label and pick an option by its text.
const choose = (label, option) => {
  fireEvent.mouseDown(screen.getByLabelText(label));
  fireEvent.click(screen.getByRole("option", { name: option }));
};

describe("WorldActorsPanel", () => {
  beforeEach(() => {
    mutate.mockReset();
    actorsData = cast([MANAGER]);
  });

  it("lists the cast with what each may do, when they come in, and says the changes are unsaved", () => {
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByText("Actors (1)")).toBeInTheDocument();
    expect(screen.getByText(/Priya Raman/)).toBeInTheDocument();
    expect(
      screen.getByText(
        "May do: crm.approve_discount · Comes in: From the start · If they do not answer: Nothing happens",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/Unsaved changes/)).toBeInTheDocument();
  });

  it("lists people on the caller's side apart from the world, with their channel and scenarios", () => {
    actorsData = cast([
      MANAGER,
      {
        id: "colleague",
        name: "Jordan Taylor",
        side: "caller",
        channel: "audio_mix",
        scenarios: 2,
      },
    ]);
    render(<WorldActorsPanel envId="env-1" callerSide />);
    expect(screen.getByText("Actors (2)")).toBeInTheDocument();
    expect(screen.getByText("On the caller's side")).toBeInTheDocument();
    expect(
      screen.getByText("Heard nearby · Loudness automatic · In 2 scenarios"),
    ).toBeInTheDocument();
    expect(screen.getByText("In the world")).toBeInTheDocument();
  });

  it("shows what an actor sees and never sees in plain words", () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText(/Priya Raman/));
    expect(
      screen.getByText("What they want: Approve fast"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("What they know: The limit is 25%"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "How they behave: response timing: delayed · seconds: 5",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Grader material: solution, sub goals, sub_goals.json"),
    ).toBeInTheDocument();
  });

  it("adds someone in the world with every field, picking actions from the environment's own list", () => {
    actorsData = cast([], { source: "saved", revision: 0 });
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add someone in the world"));
    for (const label of [
      "Id",
      "Name",
      "Role (optional)",
      "Kind (optional)",
      "What they want (optional)",
      "May agree to (optional, one per line)",
      "Keeps to themselves (optional, one per line)",
      "Hands over when (optional, one per line)",
      "Is done when (optional, one per line)",
      "What they know (optional, one per line)",
      "How they behave (optional)",
      "Answers",
      "World actions they may take (optional)",
      "Comes in",
      "Waits, in seconds (optional)",
      "If they do not answer",
    ])
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Id"), {
      target: { value: "manager" },
    });
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya" },
    });
    fireEvent.change(
      screen.getByLabelText("Is done when (optional, one per line)"),
      {
        target: { value: "She has decided" },
      },
    );
    fireEvent.change(
      screen.getByLabelText("What they know (optional, one per line)"),
      {
        target: { value: "The limit is 25%" },
      },
    );
    choose("Answers", "After a delay");
    fireEvent.change(screen.getByLabelText("Seconds"), {
      target: { value: "5" },
    });
    fireEvent.mouseDown(
      screen.getByLabelText("World actions they may take (optional)"),
    );
    expect(screen.getByText("Approve a pending discount")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("option", { name: /crm.approve_discount/ }),
    );
    choose("Comes in", "When the agent calls crm.request_approval");
    fireEvent.click(screen.getByText("Save to draft"));
    const [change] = mutate.mock.calls[0];
    expect(change.op).toBe("create");
    expect(change.revision).toBe(0);
    expect(change.actor).toMatchObject({
      id: "manager",
      name: "Priya",
      kind: "person",
      parent: null,
      constraints: { stop_when: ["She has decided"] },
      knowledge: [{ kind: "facts", text: "The limit is 25%" }],
      behaviour: { response_timing: { kind: "delayed", seconds: 5 } },
      permitted_actions: ["crm.approve_discount"],
      activation: {
        trigger: { kind: "on_tool", tool: "crm.request_approval" },
        fallback: "silent",
      },
    });
  });

  it("says plainly when the environment has no world actions to give", () => {
    actorsData = cast([], { actions: [] });
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add someone in the world"));
    expect(
      screen.getByText(
        /its tools run inside the agent, so no one in the world can act here/,
      ),
    ).toBeInTheDocument();
  });

  it("adds someone on the caller's side with a channel and its loudness", () => {
    actorsData = cast([]);
    render(<WorldActorsPanel envId="env-1" callerSide />);
    fireEvent.click(screen.getByText("Add someone on the caller's side"));
    expect(
      screen.queryByLabelText("World actions they may take (optional)"),
    ).toBeNull();
    fireEvent.change(screen.getByLabelText("Id"), {
      target: { value: "colleague" },
    });
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Jordan" },
    });
    // Automatic by default; switching it off fixes a share, set on a slider.
    const automatic = screen.getByRole("checkbox", {
      name: /Automatic: each call picks between 33% and 67%/,
    });
    expect(automatic).toBeChecked();
    fireEvent.click(automatic);
    expect(screen.getByRole("slider")).toHaveValue("0.5");
    expect(screen.getByText("50% of the caller's voice")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Save to draft"));
    expect(mutate.mock.calls[0][0].actor).toMatchObject({
      id: "colleague",
      name: "Jordan",
      side: "caller",
      channel: "audio_mix",
      loudness_ratio: 0.5,
    });
  });

  it("makes a new actor's id from its name, unique in the cast, and says how loud is automatic", () => {
    actorsData = cast([{ id: "sam_lee", name: "Sam Lee", side: "world" }]);
    render(<WorldActorsPanel envId="env-1" callerSide />);
    fireEvent.click(screen.getByText("Add someone in the world"));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Sam Lee" },
    });
    expect(screen.getByLabelText("Id")).toHaveValue("sam_lee_2");
    fireEvent.click(screen.getByText("Save to draft"));
    expect(mutate.mock.calls[0][0].actor.id).toBe("sam_lee_2");
    fireEvent.click(screen.getByText("Cancel"));
    fireEvent.click(screen.getByText("Add someone on the caller's side"));
    expect(
      screen.getByText(/Automatic: each call picks between 33% and 67%/),
    ).toBeInTheDocument();
  });

  it("says who already has a typed id and will not save it, and offers no one to be brought in by when the world is empty", () => {
    actorsData = cast([
      { id: "colleague", name: "Jordan Taylor", channel: "audio_mix" },
    ]);
    render(<WorldActorsPanel envId="env-1" callerSide />);
    fireEvent.click(screen.getByText("Add someone in the world"));
    expect(screen.queryByLabelText("Brought in by (optional)")).toBeNull();
    fireEvent.change(screen.getByLabelText("Id"), {
      target: { value: "colleague" },
    });
    expect(
      screen.getByText("Already used by Jordan Taylor; choose another id"),
    ).toBeInTheDocument();
    expect(screen.getByText("Save to draft").closest("button")).toBeDisabled();
  });

  it("opens a fresh form for the next actor after a save", () => {
    mutate.mockImplementation((_change, options) => options?.onSuccess?.());
    actorsData = cast([]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add someone in the world"));
    fireEvent.change(screen.getByLabelText("Role (optional)"), {
      target: { value: "store manager" },
    });
    fireEvent.click(screen.getByText("Save to draft"));
    fireEvent.click(screen.getByText("Add someone in the world"));
    expect(screen.getByLabelText("Role (optional)")).toHaveValue("");
  });

  it("keeps an actor's trigger when only another field is edited", () => {
    const delayed = { kind: "after_seconds", seconds: 30 };
    actorsData = {
      actors: [
        { ...MANAGER, activation: { trigger: delayed, fallback: "silent" } },
      ],
      source: "draft",
      revision: 3,
    };
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Edit"));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya R." },
    });
    fireEvent.click(screen.getByText("Save to draft"));
    const [change] = mutate.mock.calls[0];
    expect(change.actor.name).toBe("Priya R.");
    expect(change.actor.activation.trigger).toEqual(delayed);
  });

  it("removes an actor from the draft, and a locked environment offers no edits", () => {
    const { unmount } = render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Remove"));
    expect(mutate.mock.calls[0][0]).toEqual({
      op: "delete",
      actorId: "manager",
      revision: 3,
    });
    unmount();
    render(<WorldActorsPanel envId="env-1" locked />);
    expect(screen.queryByText("Add someone in the world")).toBeNull();
    expect(screen.queryByText("Remove")).toBeNull();
  });
});
