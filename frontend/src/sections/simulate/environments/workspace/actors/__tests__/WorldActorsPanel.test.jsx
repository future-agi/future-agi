import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "src/utils/test-utils";

const mutate = vi.fn();
const resetEdit = vi.fn();
const refetch = vi.fn();
let editState;
let actorsData;
let actorsState;
// The save tests run the real hooks against a mocked axios; the rest drive the panel through these stand-ins.
let realHooks = false;
let viewState;

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

vi.mock("src/api/simulate-environments/versions", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    useActors: (...args) =>
      realHooks
        ? real.useActors(...args)
        : { data: actorsData, refetch, ...actorsState },
    useEditActor: (...args) =>
      realHooks
        ? real.useEditActor(...args)
        : { mutate, reset: resetEdit, ...editState },
    useSetActorLimits: (...args) =>
      realHooks
        ? real.useSetActorLimits(...args)
        : { mutate: vi.fn(), reset: vi.fn() },
    useUploadEnvironmentFile: (...args) =>
      realHooks
        ? real.useUploadEnvironmentFile(...args)
        : { mutateAsync: vi.fn(), reset: vi.fn() },
    useActorView: () =>
      viewState ?? {
        data: {
          sees: [
            { input: "actor.goal", value: "Approve fast" },
            {
              input: "actor.constraints",
              value: {
                may_agree: [],
                private: [],
                escalate_when: [],
                stop_when: [],
              },
            },
            {
              input: "actor.knowledge",
              value: [{ kind: "facts", text: "The limit is 25%" }],
            },
            {
              input: "actor.behaviour",
              value: { response_timing: { kind: "delayed", seconds: 5 } },
            },
          ],
          never_sees: {
            scenario_fields: ["solution", "sub_goals"],
            files: ["sub_goals.json"],
            other_actors: [
              "Jordan.goal",
              "Jordan.prompt",
              "Sam.goal",
              "Sam.prompt",
              "Ravi.goal",
              "Ravi.prompt",
            ],
          },
        },
      },
  };
});

const { default: axios } = await import("src/utils/axios");
const { actorsKey } = await import("src/api/simulate-environments/versions");
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
  record_schemas: {
    "actors.json": {
      properties: {
        id: {
          pattern: "^[a-z0-9_]{1,64}$",
          description: "1 to 64 lowercase letters, digits or underscores",
        },
      },
    },
  },
  ...extra,
});
// Open an MUI select by its label and pick an option by its text.
const choose = (label, option) => {
  fireEvent.mouseDown(screen.getByLabelText(label));
  fireEvent.click(screen.getByRole("option", { name: option }));
};

describe("WorldActorsPanel saving against the real hooks", () => {
  const DRAFT_CHANGED = {
    error: "draft_changed",
    message: "the actors were edited elsewhere since you read them",
    statusCode: 409,
  };
  const OPENED = cast([MANAGER]);
  const body = (call) => axios.put.mock.calls[call][1];
  let client;

  const openEdit = async () => {
    render(
      <QueryClientProvider client={client}>
        <WorldActorsPanel envId="env-1" />
      </QueryClientProvider>,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit Priya Raman" }),
    );
    fireEvent.change(screen.getByLabelText("Role (optional)"), {
      target: { value: "regional manager" },
    });
  };

  beforeEach(() => {
    realHooks = true;
    client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    axios.get.mockReset();
    axios.put.mockReset();
    axios.get.mockResolvedValue({ data: OPENED });
    return () => {
      realHooks = false;
    };
  });

  it("sends the revision the form opened at, then rebases on the fresh record so the retry keeps both edits", async () => {
    const elsewhere = cast(
      [{ ...MANAGER, goal: "Approve only large discounts" }],
      { revision: 4 },
    );
    await openEdit();
    // A window-focus refetch moves the cast under the open form.
    axios.get.mockResolvedValue({ data: elsewhere });
    await client.invalidateQueries({ queryKey: actorsKey("env-1") });
    await screen.findByText(/Wants: Approve only large discounts/);
    axios.put.mockRejectedValueOnce(DRAFT_CHANGED);

    fireEvent.click(screen.getByRole("button", { name: "Save to draft" }));
    await screen.findByText(/changed elsewhere/);
    expect(body(0).expected_revision).toBe(3);
    expect(body(0).actor.goal).toBe(MANAGER.goal);
    expect(screen.getByLabelText("Role (optional)")).toHaveValue(
      "regional manager",
    );

    axios.put.mockResolvedValueOnce({ data: elsewhere });
    fireEvent.click(screen.getByRole("button", { name: "Save to draft" }));
    await waitFor(() => expect(screen.queryByText("Save to draft")).toBeNull());
    expect(body(1).expected_revision).toBe(4);
    expect(body(1).actor).toMatchObject({
      role: "regional manager",
      goal: "Approve only large discounts",
    });
  });

  it("sends one save for a double click, so the second click is never refused as someone else's edit", async () => {
    await openEdit();
    let answer;
    axios.put.mockImplementation(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    const save = screen.getByRole("button", { name: "Save to draft" });
    fireEvent.click(save);
    fireEvent.click(save);
    await waitFor(() => expect(axios.put).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 20));
    answer({ data: cast([MANAGER], { revision: 4 }) });
    await waitFor(() => expect(screen.queryByText("Save to draft")).toBeNull());
    expect(axios.put).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/changed elsewhere/)).toBeNull();
  });

  it("saves on top of a document uploaded from the open form, in one request at the upload's revision", async () => {
    const uploaded = cast([MANAGER], {
      revision: 4,
      knowledge_sources: { files: ["knowledge/policy.md"], records: [] },
    });
    axios.get.mockResolvedValue({
      data: cast([MANAGER], { knowledge_sources: { files: [], records: [] } }),
    });
    axios.post.mockReset();
    axios.post.mockResolvedValueOnce({ data: uploaded });
    await openEdit();
    const picker = document.querySelector('input[type="file"]');
    fireEvent.change(picker, {
      target: {
        files: [new File(["# Policy"], "policy.md", { type: "text/markdown" })],
      },
    });
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    // Sent at the revision the form opened at.
    expect(axios.post.mock.calls[0][1].get("expected_revision")).toBe("3");
    await screen.findByText("knowledge/policy.md");
    axios.put.mockResolvedValueOnce({ data: uploaded });
    fireEvent.click(screen.getByRole("button", { name: "Save to draft" }));
    await waitFor(() => expect(screen.queryByText("Save to draft")).toBeNull());
    expect(axios.put).toHaveBeenCalledTimes(1);
    expect(body(0).expected_revision).toBe(4);
    expect(screen.queryByText(/changed elsewhere/)).toBeNull();
  });

  it.each([
    [
      {
        error: "upload_invalid",
        message: "notes.pdf: the format is one of .md, .txt, .json, .csv",
        statusCode: 400,
      },
      "notes.pdf: the format is one of .md, .txt, .json, .csv",
    ],
    [{ message: "Network Error" }, "The document could not be uploaded"],
  ])(
    "says why an upload was refused in the platform's words, never a transport message",
    async (failure, shown) => {
      axios.post.mockReset();
      axios.post.mockRejectedValueOnce(failure);
      await openEdit();
      fireEvent.change(document.querySelector('input[type="file"]'), {
        target: { files: [new File(["x"], "notes.pdf")] },
      });
      expect(await screen.findByText(shown)).toBeInTheDocument();
    },
  );

  it("says so and won't save when the actor was removed elsewhere", async () => {
    await openEdit();
    axios.put.mockRejectedValueOnce(DRAFT_CHANGED);
    axios.get.mockResolvedValue({ data: cast([], { revision: 4 }) });
    fireEvent.click(screen.getByRole("button", { name: "Save to draft" }));
    await screen.findByText(/removed elsewhere/);
    expect(screen.getByLabelText("Role (optional)")).toHaveValue(
      "regional manager",
    );
    expect(
      screen.getByRole("button", { name: "Save to draft" }),
    ).toBeDisabled();
  });
});

describe("WorldActorsPanel", () => {
  beforeEach(() => {
    mutate.mockReset();
    resetEdit.mockReset();
    refetch.mockReset();
    editState = { isPending: false, error: null };
    actorsData = cast([MANAGER]);
    actorsState = { isLoading: false, isError: false };
    viewState = null;
  });

  it("shows a skeleton of the groups while loading, with no count and nothing to add yet", () => {
    actorsData = undefined;
    actorsState = { isLoading: true, isError: false };
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the actors…");
    expect(screen.queryByRole("progressbar")).toBeNull();
    // One list for every actor: no caller-side or world group.
    expect(screen.getByText("Actors")).toBeInTheDocument();
    expect(screen.queryByText("On the caller's side")).toBeNull();
    expect(screen.queryByText("Add an actor")).toBeNull();
  });

  it("says the actors could not be loaded, offers a retry and nothing to add", () => {
    actorsData = undefined;
    actorsState = { isLoading: false, isError: true };
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByText("Couldn't load the actors")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
    expect(screen.queryByText("Add an actor")).toBeNull();
  });

  it("keeps a cast already read when a refresh fails", () => {
    actorsState = { isLoading: false, isError: true };
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByText("Actors (1)")).toBeInTheDocument();
    expect(screen.getByText("Add an actor")).toBeInTheDocument();
  });

  it("checks a typed id against the id rule the cast is sent with", () => {
    actorsData = cast([], {
      record_schemas: {
        "actors.json": { properties: { id: { pattern: "^[a-z]{1,3}$" } } },
      },
    });
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    fireEvent.change(screen.getByLabelText(/^Name/), {
      target: { value: "Sam" },
    });
    fireEvent.change(screen.getByLabelText("Id"), {
      target: { value: "abcd" },
    });
    expect(
      screen.getByRole("button", { name: "Save to draft" }),
    ).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Id"), {
      target: { value: "abc" },
    });
    expect(
      screen.getByRole("button", { name: "Save to draft" }),
    ).not.toBeDisabled();
  });

  it("lists the cast with what each may do, when they come in, and says the changes are unsaved", () => {
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByText("Actors (1)")).toBeInTheDocument();
    expect(screen.getByText(/Priya Raman/)).toBeInTheDocument();
    expect(
      screen.getByText(
        "May do: crm.approve_discount · Comes in: From the start · If they do not answer: Nothing happens · In 0 scenarios",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/Unsaved changes/)).toBeInTheDocument();
  });

  it("says a fallback action is taken for the actor, and lists a record change as one of the world's actions", () => {
    actorsData = cast(
      [
        {
          ...MANAGER,
          permitted_actions: ["approvals.change"],
          activation: { fallback: "approvals.change" },
        },
      ],
      {
        actions: [
          ...ACTIONS,
          {
            action: "approvals.change",
            tool: "",
            description: "A record change on approvals",
          },
        ],
      },
    );
    render(<WorldActorsPanel envId="env-1" />);
    expect(
      screen.getByText(
        "May do: approvals.change · Comes in: From the start · If they do not answer: approvals.change is taken for them · In 0 scenarios",
      ),
    ).toBeInTheDocument();
  });

  it("names the actor who answers instead by its name, not its id", () => {
    actorsData = cast([
      { ...MANAGER, activation: { fallback: "lead_one" } },
      { id: "lead_one", name: "Sam Rivera", role: "Sales Lead" },
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    expect(
      screen.getByText(/If they do not answer: Sam Rivera answers instead/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/lead_one answers instead/)).toBeNull();
  });

  it("shows who can reach whom open once there is more than one actor, with limits folded", () => {
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.queryByText("Who can reach whom")).toBeNull();
    actorsData = cast([
      MANAGER,
      { id: "caller", name: "Dana", role: "customer" },
    ]);
    const { container } = render(<WorldActorsPanel envId="env-2" />);
    expect(
      within(container).getByText("Who can reach whom"),
    ).toBeInTheDocument();
    expect(
      within(container).getByRole("button", { name: "Limits" }),
    ).toBeInTheDocument();
    expect(within(container).queryByText("Save limits to draft")).toBeNull();
  });

  it("says nothing about a missing caller while there are no actors at all", () => {
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByText(/no caller record yet/)).toBeInTheDocument();
    actorsData = cast([]);
    const { container } = render(<WorldActorsPanel envId="env-2" />);
    expect(within(container).queryByText(/no caller record yet/)).toBeNull();
  });

  it("lists every actor in one list, one that speaks with its channel beside what it may do", () => {
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
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByText("Actors (2)")).toBeInTheDocument();
    expect(screen.queryByText(/On the caller's side/)).toBeNull();
    expect(
      screen.getByText(
        "Heard nearby · Loudness automatic · May do: Nothing in the world · Comes in: From the start · If they do not answer: Nothing happens · In 2 scenarios",
      ),
    ).toBeInTheDocument();
  });

  it("shows what an actor sees and never sees in plain words", () => {
    render(<WorldActorsPanel envId="env-1" locked />);
    fireEvent.click(screen.getByText("What they see"));
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
      screen.getByText("Grader material: solution, sub goals"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "What the 3 other actors each keep to themselves: goal, prompt",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("Hide what they see"));
    expect(screen.getByText("What they see")).toBeInTheDocument();
  });

  it("adds someone in the world with every field, picking actions from the environment's own list", () => {
    actorsData = cast([], { source: "saved", revision: 0 });
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
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
    choose("Comes in", "When the agent calls an action");
    choose("The action", "crm.request_approval");
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

  it("shows the conflict before saving when an actor is given an action only one other actor holds, and saves it shared only once confirmed", () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Lena" },
    });
    fireEvent.mouseDown(
      screen.getByLabelText("World actions they may take (optional)"),
    );
    fireEvent.click(
      screen.getByRole("option", { name: /crm.approve_discount/ }),
    );
    const warning = screen.getByTestId("actor-permission-conflict");
    expect(warning).toHaveTextContent(
      "crm.approve_discount is held only by Priya Raman today",
    );
    const save = screen.getByText("Save to draft");
    expect(save.closest("button")).toBeDisabled();
    fireEvent.click(within(warning).getByLabelText("Both should have it"));
    fireEvent.click(save);
    const [change] = mutate.mock.calls[0];
    expect(change.actor.permitted_actions).toEqual(["crm.approve_discount"]);
    expect(change.shareActions).toEqual(["crm.approve_discount"]);
  });

  it("needs the confirmation again when another reserved action is added after ticking", () => {
    actorsData = cast([
      MANAGER,
      {
        id: "ops",
        name: "Ops Lead",
        role: "operations",
        permitted_actions: ["crm.request_approval"],
      },
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Lena" },
    });
    const field = screen.getByLabelText(
      "World actions they may take (optional)",
    );
    fireEvent.mouseDown(field);
    fireEvent.click(
      screen.getByRole("option", { name: /crm.approve_discount/ }),
    );
    fireEvent.click(
      within(screen.getByTestId("actor-permission-conflict")).getByLabelText(
        "Both should have it",
      ),
    );
    const save = screen.getByText("Save to draft").closest("button");
    expect(save).not.toBeDisabled();
    fireEvent.mouseDown(field);
    fireEvent.click(
      screen.getByRole("option", { name: /crm.request_approval/ }),
    );
    // The new reserved action was never confirmed: Save waits for the box again.
    expect(save).toBeDisabled();
    expect(
      within(screen.getByTestId("actor-permission-conflict")).getByLabelText(
        "Both should have it",
      ),
    ).not.toBeChecked();
  });

  it("never makes or accepts an id the environment keeps for itself", () => {
    actorsData = cast([MANAGER], { reserved_ids: ["agent", "caller"] });
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Agent" },
    });
    // The id made from the name steps past the reserved one.
    expect(screen.getByLabelText("Id")).toHaveAttribute(
      "placeholder",
      "agent_2",
    );
    fireEvent.change(screen.getByLabelText("Id"), {
      target: { value: "caller" },
    });
    expect(
      screen.getByText(/Already used by the environment itself/),
    ).toBeInTheDocument();
    expect(screen.getByText("Save to draft").closest("button")).toBeDisabled();
  });

  it("shows no conflict for an action nobody else holds", () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Lena" },
    });
    fireEvent.mouseDown(
      screen.getByLabelText("World actions they may take (optional)"),
    );
    fireEvent.click(
      screen.getByRole("option", { name: /crm.request_approval/ }),
    );
    expect(
      screen.queryByTestId("actor-permission-conflict"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Save to draft"));
    expect(mutate.mock.calls[0][0].shareActions).toEqual([]);
  });

  it("keeps the details a run does not read yet folded under More details", () => {
    actorsData = cast([]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    expect(
      screen.getByLabelText("What they know (optional, one per line)"),
    ).toBeVisible();
    expect(
      screen.getByLabelText("What they want (optional)"),
    ).not.toBeVisible();
    fireEvent.click(screen.getByText("More details"));
    expect(screen.getByLabelText("What they want (optional)")).toBeVisible();
    // A live or speaking actor is given these; the form never says they are unused.
    expect(
      screen.getByText(
        /Given to them as background whenever they speak or decide in the moment/,
      ),
    ).toBeVisible();
    expect(screen.queryByText(/not used by runs/)).toBeNull();
  });

  it("needs a role for an actor who speaks when the record schema says so", () => {
    actorsData = cast([], {
      record_schemas: {
        "actors.json": {
          properties: { id: { pattern: "^[a-z_]{1,64}$" } },
          then: { required: ["role"] },
        },
      },
    });
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    fireEvent.change(screen.getByLabelText(/^Name/), {
      target: { value: "Jordan" },
    });
    const save = screen.getByRole("button", { name: "Save to draft" });
    // Without a channel it does not speak, so no role is needed.
    expect(save).not.toBeDisabled();
    choose("How they join the conversation", "Heard nearby");
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Role"), {
      target: { value: "the caller's colleague" },
    });
    expect(save).not.toBeDisabled();
  });

  it("adds an actor who speaks with a channel and its loudness, and every other field too", () => {
    actorsData = cast([]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    // The same fields as any actor: what it may do in the world is offered to a speaking actor too.
    expect(
      screen.getByLabelText("World actions they may take (optional)"),
    ).toBeInTheDocument();
    choose("How they join the conversation", "Heard nearby");
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
    choose("Gender", "Female");
    choose("Age", "50 to 59");
    fireEvent.click(screen.getByText("Save to draft"));
    expect(mutate.mock.calls[0][0].actor).toMatchObject({
      id: "colleague",
      name: "Jordan",
      channel: "audio_mix",
      loudness_ratio: 0.5,
      persona: { gender: "female", age_group: "50-59" },
    });
    expect(mutate.mock.calls[0][0].actor.persona).not.toHaveProperty("accent");
    expect(mutate.mock.calls[0][0].actor).not.toHaveProperty("side");
  });

  it("changes how an actor sounds and keeps the rest of its persona", () => {
    actorsData = cast([
      {
        id: "colleague",
        name: "Jordan Taylor",
        channel: "audio_handoff",
        persona: { gender: "male", age_group: "32-40", occupation: "Engineer" },
      },
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Jordan Taylor"));
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    expect(screen.getByLabelText("Age")).toHaveTextContent("32-40");
    choose("Accent", "British");
    fireEvent.click(screen.getByText("Save to draft"));
    expect(mutate.mock.calls[0][0].actor.persona).toEqual({
      gender: "male",
      age_group: "32-40",
      occupation: "Engineer",
      accent: "British",
    });
  });

  it("makes a new actor's id from its name, unique in the cast, and says how loud is automatic", () => {
    actorsData = cast([{ id: "sam_lee", name: "Sam Lee" }]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Sam Lee" },
    });
    expect(screen.getByLabelText("Id")).toHaveValue("sam_lee_2");
    fireEvent.click(screen.getByText("Save to draft"));
    expect(mutate.mock.calls[0][0].actor.id).toBe("sam_lee_2");
    fireEvent.click(screen.getByText("Cancel"));
    fireEvent.click(screen.getByText("Add an actor"));
    choose("How they join the conversation", "Heard nearby");
    expect(
      screen.getByText(/Automatic: each call picks between 33% and 67%/),
    ).toBeInTheDocument();
  });

  it("keeps a role within the length its record schema sets and says so at the limit", () => {
    actorsData = cast([MANAGER], {
      record_schemas: {
        "actors.json": { properties: { role: { maxLength: 12 } } },
      },
    });
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    const role = screen.getByLabelText("Role (optional)");
    expect(role).toHaveAttribute("maxLength", "12");
    fireEvent.change(role, { target: { value: "twelve chars" } });
    expect(screen.getByText("At most 12 characters")).toBeInTheDocument();
  });

  it("says who already has a typed id and will not save it, and offers any actor, one who speaks too, to be brought in by", () => {
    actorsData = cast([
      { id: "colleague", name: "Jordan Taylor", channel: "audio_mix" },
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    expect(
      screen.getByLabelText("Brought in by (optional)"),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Id"), {
      target: { value: "colleague" },
    });
    expect(
      screen.getByText("Already used by Jordan Taylor; choose another id"),
    ).toBeInTheDocument();
    expect(screen.getByText("Save to draft").closest("button")).toBeDisabled();
  });

  it("uses the id made from the name when a typed id is cleared", () => {
    actorsData = cast([]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    fireEvent.change(screen.getByLabelText("Id"), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText("Id"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Ravi Kumar" },
    });
    expect(screen.getByLabelText("Id")).toHaveAttribute(
      "placeholder",
      "ravi_kumar",
    );
    fireEvent.click(screen.getByText("Save to draft"));
    expect(mutate.mock.calls[0][0].actor.id).toBe("ravi_kumar");
  });

  it("opens a fresh form for the next actor after a save", () => {
    mutate.mockImplementation((_change, options) => options?.onSuccess?.());
    actorsData = cast([]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya Raman" },
    });
    fireEvent.change(screen.getByLabelText("Role (optional)"), {
      target: { value: "store manager" },
    });
    fireEvent.click(screen.getByText("Save to draft"));
    fireEvent.click(screen.getByText("Add an actor"));
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
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya R." },
    });
    fireEvent.click(screen.getByText("Save to draft"));
    const [change] = mutate.mock.calls[0];
    expect(change.actor.name).toBe("Priya R.");
    expect(change.actor.activation.trigger).toEqual(delayed);
  });

  it("keeps Save off for an existing actor until something really changes", () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    const save = screen.getByRole("button", { name: "Save to draft" });
    expect(save).toBeDisabled();
    const name = screen.getByLabelText("Name");
    fireEvent.change(name, { target: { value: "Priya R." } });
    expect(save).not.toBeDisabled();
    fireEvent.change(name, { target: { value: "Priya Raman " } });
    expect(save).toBeDisabled();
  });

  it("names a trigger of any kind, and going back to it in Comes in is no change", async () => {
    actorsData = cast([
      {
        ...MANAGER,
        activation: { trigger: { kind: "after_seconds", seconds: 30 } },
      },
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByText(/Comes in: After 30 seconds/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    expect(screen.getByLabelText("Comes in")).toHaveTextContent(
      "At a set time",
    );
    expect(screen.getByLabelText("Seconds after the start")).toHaveValue(30);
    const save = screen.getByRole("button", { name: "Save to draft" });
    choose("Comes in", "When the agent contacts them");
    expect(save).not.toBeDisabled();
    // The first menu is gone before the select can be opened again.
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    choose("Comes in", "At a set time");
    expect(save).toBeDisabled();
  });

  it("keeps Save off while the way an actor comes in is missing what it needs, and says what", async () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    const save = screen.getByRole("button", { name: "Save to draft" });
    choose("Comes in", "When the agent calls an action");
    expect(save).toBeDisabled();
    expect(
      screen.getByText("Pick the action that brings them in"),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    choose("The action", "crm.approve_discount");
    expect(save).not.toBeDisabled();
  });

  it("keeps Save off for a cap that is not a whole number, and saves the number as typed", () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    const save = screen.getByRole("button", { name: "Save to draft" });
    const cap = screen.getByLabelText("Most turns in a call (optional)");
    fireEvent.change(cap, { target: { value: "2.7" } });
    expect(save).toBeDisabled();
    expect(
      screen.getByText("A whole number above 0, or empty for no limit"),
    ).toBeInTheDocument();
    fireEvent.change(cap, { target: { value: "3" } });
    fireEvent.click(save);
    expect(mutate.mock.calls[0][0].actor.max_turns).toBe(3);
  });

  it("shows a record's value even when its select doesn't list it, and an empty value by its own word", () => {
    actorsData = cast([
      {
        ...MANAGER,
        kind: "robot",
        parent: "colleague",
        activation: { fallback: "colleague" },
      },
      { id: "colleague", name: "Jordan", channel: "audio_mix" },
    ]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Priya Raman" }));
    expect(screen.getByLabelText("Kind (optional)")).toHaveTextContent("robot");
    expect(screen.getByLabelText("If they do not answer")).toHaveTextContent(
      "Jordan answers instead",
    );
    expect(screen.getByLabelText("Brought in by (optional)")).toHaveTextContent(
      "Jordan",
    );
    expect(screen.getByLabelText("Comes in")).toHaveTextContent(
      "Present from the start",
    );
  });

  it("shows no id before a name is typed, and still saves the fallback for a name with no letters", () => {
    actorsData = cast([]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    expect(screen.getByLabelText("Id")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "!!!" },
    });
    expect(screen.getByLabelText("Id")).toHaveValue("actor");
    fireEvent.click(screen.getByText("Save to draft"));
    expect(mutate.mock.calls[0][0].actor.id).toBe("actor");
  });

  it("words the id rule as the schema describes it", () => {
    actorsData = cast([], {
      record_schemas: {
        "actors.json": {
          properties: {
            id: { pattern: "^[a-z]{1,3}$", description: "up to 3 letters" },
          },
        },
      },
    });
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    fireEvent.change(screen.getByLabelText("Id"), { target: { value: "ab" } });
    expect(screen.getByText(/Up to 3 letters\./)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Id"), {
      target: { value: "abcd" },
    });
    expect(screen.getByText("Use up to 3 letters")).toBeInTheDocument();
  });

  it("shows a loading and a failed view inside the row, with a retry", () => {
    viewState = { isLoading: true };
    const { rerender } = render(<WorldActorsPanel envId="env-1" locked />);
    fireEvent.click(screen.getByText("What they see"));
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    const retry = vi.fn();
    viewState = { isError: true, refetch: retry };
    rerender(<WorldActorsPanel envId="env-1" locked />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalled();
  });

  it.each([
    ["a gateway page with no sentence", { statusCode: 502 }, /Couldn't save/],
    ["a network failure", { message: "Network Error" }, /Couldn't save/],
    [
      "a refusal the server worded",
      { statusCode: 400, error: "actor_invalid", message: "Role is too long" },
      /Role is too long/,
    ],
  ])(
    "says what went wrong in the form and on the row: %s",
    (_, error, words) => {
      editState = { isPending: false, error };
      const { rerender } = render(<WorldActorsPanel envId="env-1" />);
      expect(screen.getByText(words)).toBeInTheDocument();
      editState = { isPending: false, error: null };
      rerender(<WorldActorsPanel envId="env-1" />);
      fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
      editState = { isPending: false, error };
      rerender(<WorldActorsPanel envId="env-1" />);
      expect(screen.getByText(words)).toBeInTheDocument();
    },
  );

  it("clears the in-use warning when a form opens", () => {
    actorsData = cast([{ ...MANAGER, scenarios: 2 }]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Remove / }));
    expect(screen.getByText(/is in 2 scenarios/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    expect(screen.queryByText(/is in 2 scenarios/)).toBeNull();
  });

  it("removes an actor from the draft once confirmed, and a locked environment offers no edits", () => {
    const { unmount } = render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Remove / }));
    expect(mutate).not.toHaveBeenCalled();
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Remove",
      }),
    );
    expect(mutate.mock.calls[0][0]).toEqual({
      op: "delete",
      actorId: "manager",
      revision: 3,
    });
    unmount();
    render(<WorldActorsPanel envId="env-1" locked />);
    expect(screen.queryByText("Add an actor")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Remove / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
    expect(
      screen.getByRole("button", { name: "What they see" }),
    ).toBeInTheDocument();
  });

  it("offers no other edit while a remove is in flight, so its answer can't be lost", () => {
    editState = {
      isPending: true,
      error: null,
      variables: { op: "delete", actorId: "manager" },
    };
    render(<WorldActorsPanel envId="env-1" />);
    expect(screen.getByRole("button", { name: /^Edit / })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^Remove / })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add an actor" })).toBeDisabled();
  });

  it("still asks when an existing value was emptied and then folded away", async () => {
    actorsData = cast([{ ...MANAGER, goal: "Approve refunds" }]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Edit / }));
    const more = screen.getByRole("button", { name: /more details/i });
    fireEvent.click(more);
    fireEvent.change(
      screen.getByRole("textbox", { name: "What they want (optional)" }),
      { target: { value: "" } },
    );
    fireEvent.click(more);
    await waitFor(() =>
      expect(
        screen.queryByRole("textbox", { name: "What they want (optional)" }),
      ).toBeNull(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(
      screen.getByRole("dialog", { name: /Discard changes/ }),
    ).toBeInTheDocument();
  });

  it("warns instead of asking when the actor is in a scenario", () => {
    actorsData = cast([{ ...MANAGER, scenarios: 2 }]);
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: /^Remove / }));
    expect(screen.getByText(/is in 2 scenarios/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mutate).not.toHaveBeenCalled();
  });

  it("offers the full name in a tooltip only when it is cut off", async () => {
    render(<WorldActorsPanel envId="env-1" locked />);
    const name = screen.getByText(/Priya Raman/);
    fireEvent.mouseEnter(name);
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.mouseLeave(name);
    Object.defineProperty(name, "scrollWidth", { value: 200 });
    Object.defineProperty(name, "clientWidth", { value: 100 });
    fireEvent.mouseEnter(name);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Priya Raman");
  });

  it("asks before closing a form with typed values, and keeps them if you stay", async () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    const name = screen.getByRole("textbox", { name: "Name" });
    fireEvent.change(name, { target: { value: "Sam Lee" } });
    fireEvent.keyDown(name, { key: "Escape" });
    const dialog = screen.getByRole("dialog", { name: /Discard changes/ });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    // The drawer is hidden from assistive tech until the prompt has finished closing.
    expect(await screen.findByRole("textbox", { name: "Name" })).toHaveValue(
      "Sam Lee",
    );
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: /Discard changes/ })).getByRole(
        "button",
        { name: "Discard" },
      ),
    );
    expect(screen.queryByText("Save to draft")).toBeNull();
  });

  it("closes straight away with nothing typed, and when Cancel is pressed", () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText("Save to draft")).toBeNull();
    fireEvent.click(screen.getByText("Add an actor"));
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Sam Lee" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(
      screen.queryByRole("dialog", { name: /Discard changes/ }),
    ).toBeNull();
    expect(screen.queryByText("Save to draft")).toBeNull();
  });

  it("clears a failed remove's error when a form opens, and it can be dismissed", () => {
    editState = {
      isPending: false,
      error: new Error("fraud_desk depends on them"),
    };
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(resetEdit).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText("Add an actor"));
    expect(resetEdit).toHaveBeenCalledTimes(2);
  });

  it("leaves an open view open when a remove is cancelled", async () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("What they see"));
    fireEvent.click(screen.getByRole("button", { name: /^Remove / }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Cancel",
      }),
    );
    expect(await screen.findByText("Hide what they see")).toBeInTheDocument();
  });

  it("will not close or discard the form while a save is in flight", () => {
    const { rerender } = render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Sam Lee" },
    });
    editState = { isPending: true, error: null };
    rerender(<WorldActorsPanel envId="env-1" />);
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Name" }), {
      key: "Escape",
    });
    expect(
      screen.queryByRole("dialog", { name: /Discard changes/ }),
    ).toBeNull();
    expect(screen.getByText("Save to draft")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  it("does not ask once typed text has been deleted again", () => {
    render(<WorldActorsPanel envId="env-1" />);
    fireEvent.click(screen.getByText("Add an actor"));
    const name = screen.getByRole("textbox", { name: "Name" });
    fireEvent.change(name, { target: { value: "Sam Lee" } });
    fireEvent.change(name, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(
      screen.queryByRole("dialog", { name: /Discard changes/ }),
    ).toBeNull();
    expect(screen.queryByText("Save to draft")).toBeNull();
  });

  it("shows Not set for a value with nothing in it", () => {
    render(<WorldActorsPanel envId="env-1" locked />);
    fireEvent.click(screen.getByText("What they see"));
    expect(screen.getByText("Not set").parentElement).toHaveTextContent(
      "What they may agree to and keep to themselves: Not set",
    );
  });
});
