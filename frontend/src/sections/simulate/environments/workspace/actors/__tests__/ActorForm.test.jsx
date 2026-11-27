import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import {
  CHANNEL_OPTIONS,
  CLEAN,
  TRIGGER_FIELDS,
  cast,
  typedActor,
} from "./castFixtures";

// A label as typed, with or without the star a required field shows.
const starred = (text) =>
  new RegExp(`^${text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}( \\*)?$`);

let verdict;
vi.mock("src/api/simulate-environments/versions", () => ({
  // The server's answer, fixed per test.
  useActorCheck: (_envId, request) => ({
    verdict: request ? verdict : null,
    pending: false,
    isError: false,
    error: null,
    retry: vi.fn(),
  }),
}));

const { default: ActorForm } = await import("../ActorForm");

const contextOf = (data) => ({
  actions: data.actions,
  channelOptions: data.channel_options,
  triggerFields: data.trigger_fields,
  fieldOptions: data.field_options,
  loudness: data.loudness,
  uploadRules: data.upload,
  idHint: data.id_hint,
  actorModels: data.actor_models,
  knowledgeSources: data.knowledge_sources,
  others: data.actors,
});

// A world with one record collection, so "Once a record exists" can be picked.
const WITH_RECORDS = cast([], {
  knowledge_sources: {
    files: [],
    records: [{ store: "crm", collection: "approvals" }],
  },
});

// A new actor as the panel opens it: the cast's `new_actor` on its side.
const newActor = (side, channel = null, data = cast([])) => ({
  ...data.new_actor,
  side,
  channel,
});

let lastSave;
let lastClose;
const renderForm = (
  actor,
  { data = cast([]), onSave = vi.fn(), onClose = vi.fn() } = {},
) => {
  lastSave = onSave;
  lastClose = onClose;
  return render(
    <ActorForm
      envId="env-1"
      actor={actor}
      context={contextOf(data)}
      onClose={onClose}
      onSave={onSave}
    />,
  );
};
// Save is always on; a click while something blocks it saves nothing.
const refusesSave = () => {
  fireEvent.click(screen.getByRole("button", { name: "Save to draft" }));
  expect(lastSave).not.toHaveBeenCalled();
};

describe("ActorForm", () => {
  it("offers the approver kind", () => {
    verdict = CLEAN;
    renderForm(
      typedActor({
        id: "claims_supervisor",
        name: "Dana Brooks",
        kind: "approver",
      }),
    );
    expect(screen.getByText("An approver")).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByText("An approver"));
    expect(
      screen.getByRole("option", { name: "An approver" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "A person" }),
    ).toBeInTheDocument();
  });

  it("follows the design's order: how they take part, kind, role, name, id, then comes in", () => {
    verdict = CLEAN;
    renderForm(newActor("world"));
    const order = [
      "How they take part",
      "Kind (optional)",
      "Role (optional)",
      "Name",
      "Id",
      "Comes in",
    ].map((label) => screen.getByLabelText(starred(label)));
    order
      .slice(1)
      .forEach((field, index) =>
        expect(
          order[index].compareDocumentPosition(field) &
            Node.DOCUMENT_POSITION_FOLLOWING,
        ).toBeTruthy(),
      );
    expect(screen.getByText(/such as Sales manager/)).toBeInTheDocument();
    expect(screen.getByText(/such as Priya Sharma/)).toBeInTheDocument();
  });

  it("shows how a speaking actor sounds after the world actions they may take", () => {
    verdict = CLEAN;
    renderForm(newActor("caller", "audio_mix"));
    expect(
      screen
        .getByLabelText(starred("World actions they may take (optional)"))
        .compareDocumentPosition(screen.getByLabelText(starred("Gender"))) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("titles a new actor's form Add actor, added to the draft", () => {
    verdict = CLEAN;
    renderForm(newActor("caller"));
    expect(screen.getByText("Add actor")).toBeInTheDocument();
    expect(
      screen.getByText("Added to this environment's draft"),
    ).toBeInTheDocument();
  });

  it("offers the join presets the chosen way takes, and asks what brings them in when a preset covers several", () => {
    verdict = CLEAN;
    renderForm(newActor("world", null, WITH_RECORDS), { data: WITH_RECORDS });
    fireEvent.mouseDown(screen.getByLabelText(starred("Comes in")));
    expect(screen.getAllByRole("option").map((one) => one.textContent)).toEqual(
      [
        "Present from the start",
        "Joins partway",
        "When the agent contacts them",
        "At a set moment",
      ],
    );
    fireEvent.click(screen.getByRole("option", { name: "Joins partway" }));
    fireEvent.mouseDown(screen.getByLabelText(starred("What brings them in")));
    expect(screen.getAllByRole("option").map((one) => one.textContent)).toEqual(
      ["When the agent calls an action", "Once a record exists"],
    );
    fireEvent.click(
      screen.getByRole("option", { name: "Once a record exists" }),
    );
    expect(screen.getByLabelText(starred("Records of"))).toBeInTheDocument();
  });

  it("marks a way in the environment can't use as unavailable, says why, and starts a preset on one it can", async () => {
    verdict = CLEAN;
    const data = cast([], { actions: [] });
    const { unmount } = renderForm(newActor("world"), { data });
    fireEvent.mouseDown(screen.getByLabelText(starred("Comes in")));
    const partway = screen.getByRole("option", { name: /Joins partway/ });
    expect(partway).toHaveAttribute("aria-disabled", "true");
    expect(partway).toHaveTextContent(
      "Needs a world action, and this environment has none",
    );
    unmount();
    renderForm(newActor("caller", "audio_mix"), { data });
    fireEvent.mouseDown(screen.getByLabelText(starred("Comes in")));
    fireEvent.click(screen.getByRole("option", { name: "Joins partway" }));
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    expect(
      screen.getByLabelText(starred("What brings them in")),
    ).toHaveTextContent("When the agent says a word");
    fireEvent.mouseDown(screen.getByLabelText(starred("What brings them in")));
    const call = screen.getByRole("option", {
      name: /When the agent calls an action/,
    });
    expect(call).toHaveAttribute("aria-disabled", "true");
    expect(call).toHaveTextContent(
      "Needs a world action, and this environment has none",
    );
    expect(
      screen.getByRole("option", { name: /Once a record exists/ }),
    ).toHaveTextContent(
      "Needs a record collection, and this world has none yet",
    );
  });

  it("keeps a stored action trigger on screen but disabled, saying why, when the environment has no world actions", () => {
    verdict = CLEAN;
    renderForm(
      typedActor({
        id: "ops",
        name: "Ops",
        trigger: { kind: "on_tool", tool: "crm.request_approval" },
      }),
      { data: cast([], { actions: [] }) },
    );
    const action = screen.getByLabelText(starred("The action"));
    expect(action).toHaveAttribute("aria-disabled", "true");
    expect(action).toHaveAccessibleDescription(
      "This environment has no world actions, so pick another way they come in.",
    );
  });

  it("offers a chance on each call where the cast takes one, and sends the percent as a share", () => {
    verdict = CLEAN;
    const onSave = vi.fn();
    const data = cast([], {
      channel_options: CHANNEL_OPTIONS.map((one) => ({
        ...one,
        trigger_options: [
          ...one.trigger_options,
          { value: "probability", label: "By chance" },
        ],
      })),
      trigger_fields: { ...TRIGGER_FIELDS, probability: ["p"] },
    });
    renderForm(typedActor({ id: "ops", name: "Ops" }), { data, onSave });
    fireEvent.mouseDown(screen.getByLabelText(starred("Comes in")));
    fireEvent.click(
      screen.getByRole("option", { name: "A chance on each call" }),
    );
    fireEvent.change(screen.getByLabelText(starred("Chance (%)")), {
      target: { value: "25" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save to draft" }));
    expect(onSave.mock.calls[0][0].changes.trigger).toEqual({
      kind: "probability",
      p: 0.25,
    });
  });

  it("goes back to the stored way when its preset is picked again, so nothing changed", async () => {
    verdict = CLEAN;
    renderForm(
      typedActor({
        id: "lead",
        name: "Lead",
        channel: "audio_mix",
        trigger: { kind: "on_keyword", words: ["refund"] },
      }),
    );
    expect(screen.getByLabelText(starred("Comes in"))).toHaveTextContent(
      "Joins partway",
    );
    fireEvent.mouseDown(screen.getByLabelText(starred("Comes in")));
    fireEvent.click(
      screen.getByRole("option", { name: "Present from the start" }),
    );
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    fireEvent.mouseDown(screen.getByLabelText(starred("Comes in")));
    fireEvent.click(screen.getByRole("option", { name: "Joins partway" }));
    expect(
      screen.getByLabelText(starred("What brings them in")),
    ).toHaveTextContent("When the agent says a word");
    // Nothing changed: Save closes the form and saves nothing.
    fireEvent.click(screen.getByRole("button", { name: "Save to draft" }));
    expect(lastSave).not.toHaveBeenCalled();
    expect(lastClose).toHaveBeenCalled();
  });

  it("labels an actor's own cap by the field the chosen way of joining uses, as the cast says", () => {
    verdict = CLEAN;
    // The cap field follows the cast, not a rule in the form.
    const data = cast([], {
      channel_options: CHANNEL_OPTIONS.map((one) =>
        one.channel === null ? { ...one, cap_field: "max_lines" } : one,
      ),
    });
    renderForm(typedActor({ id: "ops", name: "Ops", max_lines: 3 }), { data });
    expect(
      screen.getByLabelText(starred("Most lines in a call (optional)")),
    ).toHaveValue(3);
    expect(
      screen.queryByLabelText(starred("Most turns in a call (optional)")),
    ).toBeNull();
  });

  it("offers the voice choices the cast lists, with Any for none", () => {
    verdict = CLEAN;
    renderForm(newActor("caller", "audio_mix"));
    fireEvent.mouseDown(screen.getByLabelText(starred("Age")));
    expect(screen.getAllByRole("option").map((one) => one.textContent)).toEqual(
      ["Any", "18-25", "60+"],
    );
  });

  it("names a nameless actor's form by its id", () => {
    verdict = CLEAN;
    renderForm(typedActor({ id: "ops_bot", name: "" }));
    expect(screen.getByText("Edit ops_bot")).toBeInTheDocument();
  });

  it("shows the id the server will give in a cleared id field", () => {
    verdict = { ...CLEAN, suggested_id: "sam_lee" };
    renderForm(newActor("world"));
    const id = screen.getByLabelText(starred("Id"));
    expect(id).toHaveValue("sam_lee");
    fireEvent.change(id, { target: { value: "" } });
    expect(id).toHaveValue("");
    expect(id).toHaveAttribute("placeholder", "sam_lee");
  });

  it("sends an emptied optional number as null and lets the edit save when the server finds nothing", () => {
    verdict = CLEAN;
    const onSave = vi.fn();
    renderForm(typedActor({ id: "ops", name: "Ops", timeout: 30 }), {
      onSave,
    });
    fireEvent.change(
      screen.getByLabelText(starred("Waits, in seconds (optional)")),
      {
        target: { value: "" },
      },
    );
    const save = screen.getByRole("button", { name: "Save to draft" });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(onSave.mock.calls[0][0].changes).toEqual({ timeout_seconds: null });
  });

  it("shows a server problem under the field it names, in the server's words, never a request path", () => {
    renderForm(typedActor({ id: "ops", name: "Ops" }));
    verdict = {
      ...CLEAN,
      problems: [
        { field: "trigger", message: "Pick the agent turn they come in on." },
        { field: "name", message: "Another actor is called Ops Lead." },
      ],
    };
    fireEvent.change(screen.getByLabelText(starred("Name")), {
      target: { value: "Ops Lead" },
    });
    // The edited, filled name shows its problem live; the untouched trigger waits for Save.
    expect(screen.getByLabelText(starred("Name"))).toHaveAccessibleDescription(
      "Another actor is called Ops Lead.",
    );
    expect(
      screen.queryByText("Pick the agent turn they come in on."),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save to draft" }));
    expect(
      screen.getByLabelText(starred("Comes in")),
    ).toHaveAccessibleDescription("Pick the agent turn they come in on.");
    expect(screen.queryByTestId("actor-problems")).toBeNull();
    refusesSave();
  });

  it("stars the required fields: name always, role when the way of joining needs it, and what the picked trigger takes", () => {
    verdict = CLEAN;
    // The star MUI draws inside a required field's label.
    const star = (text) =>
      screen
        .getByText(starred(text), { selector: "label" })
        .querySelector(".MuiFormLabel-asterisk");
    const { unmount } = renderForm(newActor("world", null, WITH_RECORDS), {
      data: WITH_RECORDS,
    });
    expect(star("Name")).not.toBeNull();
    expect(star("Role (optional)")).toBeNull();
    fireEvent.mouseDown(screen.getByLabelText(starred("Comes in")));
    fireEvent.click(screen.getByRole("option", { name: "Joins partway" }));
    fireEvent.mouseDown(screen.getByLabelText(starred("What brings them in")));
    fireEvent.click(
      screen.getByRole("option", { name: "Once a record exists" }),
    );
    expect(star("Records of")).not.toBeNull();
    expect(star("Matching (optional)")).toBeNull();
    unmount();
    renderForm(newActor("caller", "audio_mix"));
    expect(star("Role")).not.toBeNull();
  });

  it("opens a new actor clean, and asks for a name on its field once Save is tried", () => {
    verdict = {
      ...CLEAN,
      problems: [{ field: "name", message: "Give them a name." }],
    };
    const onSave = vi.fn();
    renderForm(newActor("world"), { onSave });
    // Editing, a dropdown included, shows no problem until Save is tried.
    fireEvent.mouseDown(screen.getByLabelText(starred("Kind (optional)")));
    fireEvent.click(screen.getByRole("option", { name: "An approver" }));
    fireEvent.change(screen.getByLabelText(starred("Role (optional)")), {
      target: { value: "Sales manager" },
    });
    expect(screen.queryByText("Give them a name.")).toBeNull();
    expect(screen.queryByTestId("actor-problems")).toBeNull();
    expect(screen.getByLabelText(starred("Name"))).not.toHaveAttribute(
      "aria-invalid",
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: "Save to draft" }));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByLabelText(starred("Name"))).toHaveAccessibleDescription(
      "Give them a name.",
    );
    refusesSave();
  });

  it("shows a too-long id's problem on the Id field, as the check answers it", () => {
    verdict = {
      problems: [
        { field: "id", message: "This must be at most 64 characters." },
      ],
      conflicts: [],
      suggested_id: null,
      over_by: 0,
    };
    renderForm(newActor("world"));
    fireEvent.change(screen.getByLabelText(starred("Name")), {
      target: { value: "Priya Raman" },
    });
    fireEvent.change(screen.getByLabelText(starred("Id")), {
      target: { value: "a".repeat(70) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save to draft" }));
    expect(screen.getByLabelText(starred("Id"))).toHaveAccessibleDescription(
      "This must be at most 64 characters.",
    );
    expect(screen.queryByText(/Couldn't check this change/)).toBeNull();
  });

  it("says Discard keeps an uploaded document in the draft", () => {
    verdict = CLEAN;
    renderForm(typedActor({ id: "ops", name: "Ops" }));
    fireEvent.change(screen.getByLabelText(starred("Name")), {
      target: { value: "Ops Lead" },
    });
    fireEvent.keyDown(screen.getByLabelText(starred("Name")), {
      key: "Escape",
    });
    expect(
      screen.getByText(/A document you uploaded is already in the draft/),
    ).toBeInTheDocument();
  });

  it("shows a problem the server has no field for above Save, and keeps Save off", () => {
    renderForm(typedActor({ id: "ops", name: "Ops" }));
    verdict = {
      ...CLEAN,
      problems: [
        {
          field: null,
          message: "ops is in a cycle of parent links; break it.",
        },
      ],
    };
    fireEvent.change(screen.getByLabelText(starred("Name")), {
      target: { value: "Ops Lead" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save to draft" }));
    expect(screen.getByTestId("actor-problems")).toHaveTextContent(
      "ops is in a cycle of parent links; break it.",
    );
    refusesSave();
  });

  it("starts a new actor from the cast's new_actor, never from defaults of its own", () => {
    verdict = CLEAN;
    const data = cast([], {
      new_actor: typedActor({
        id: "",
        name: "",
        kind: "approver",
        trigger: { kind: "contacted" },
        timeout: 45,
      }),
    });
    renderForm(newActor("world", null, data), { data });
    expect(screen.getByLabelText(starred("Kind (optional)"))).toHaveTextContent(
      "An approver",
    );
    expect(screen.getByLabelText(starred("Comes in"))).toHaveTextContent(
      "When the agent contacts them",
    );
    expect(
      screen.getByLabelText(starred("Waits, in seconds (optional)")),
    ).toHaveValue(45);
  });

  it("shows only the trigger inputs the cast lists for the picked kind, and sends them typed", () => {
    verdict = CLEAN;
    const onSave = vi.fn();
    // A release where being contacted also takes a delay.
    const data = cast([], {
      trigger_fields: { ...TRIGGER_FIELDS, contacted: ["seconds"] },
    });
    renderForm(typedActor({ id: "ops", name: "Ops" }), { data, onSave });
    fireEvent.mouseDown(screen.getByLabelText(starred("Comes in")));
    fireEvent.click(
      screen.getByRole("option", { name: "When the agent contacts them" }),
    );
    expect(
      screen.queryByLabelText(starred("From the agent's turn")),
    ).toBeNull();
    fireEvent.change(
      screen.getByLabelText(starred("Seconds after the start")),
      {
        target: { value: "12" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save to draft" }));
    expect(onSave.mock.calls[0][0].changes.trigger).toEqual({
      kind: "contacted",
      seconds: 12,
    });
  });

  it("labels each way of joining as the cast does", () => {
    verdict = CLEAN;
    const data = cast([], {
      channel_options: CHANNEL_OPTIONS.map((one) =>
        one.channel === "audio_mix"
          ? { ...one, label: "Beside the caller" }
          : one,
      ),
    });
    renderForm(newActor("caller", "audio_mix", data), { data });
    expect(
      screen.getByLabelText(starred("How they take part")),
    ).toHaveTextContent("Beside the caller");
  });

  it("lists an evaluator-only file in Files they can read, locked", () => {
    verdict = CLEAN;
    const data = cast([], {
      knowledge_sources: {
        files: ["knowledge/refunds.md"],
        records: [],
        roles: { "knowledge/answer-key.md": "evaluator_only" },
      },
    });
    renderForm(newActor("world", null, data), { data });
    fireEvent.mouseDown(
      screen.getByLabelText(starred("Files they can read (optional)")),
    );
    expect(
      screen.getByRole("option", { name: /answer-key\.md · Evaluator only/ }),
    ).toHaveAttribute("aria-disabled", "true");
    expect(
      screen.getByRole("option", { name: "knowledge/refunds.md" }),
    ).not.toHaveAttribute("aria-disabled", "true");
  });
});
