import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import {
  CHANNEL_OPTIONS,
  CLEAN,
  TRIGGER_FIELDS,
  cast,
  typedActor,
} from "./castFixtures";

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

// A new actor as the panel opens it: the cast's `new_actor` on its side.
const newActor = (side, channel = null, data = cast([])) => ({
  ...data.new_actor,
  side,
  channel,
});

const renderForm = (actor, { data = cast([]), onSave = vi.fn() } = {}) =>
  render(
    <ActorForm
      envId="env-1"
      actor={actor}
      context={contextOf(data)}
      onClose={vi.fn()}
      onSave={onSave}
    />,
  );

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

  it("titles a new actor's form Add actor, with the side it is added on", () => {
    verdict = CLEAN;
    renderForm(newActor("caller"));
    expect(screen.getByText("Add actor")).toBeInTheDocument();
    expect(
      screen.getByText(
        "On the caller's side · added to this environment's draft",
      ),
    ).toBeInTheDocument();
  });

  it("offers only the ways of coming in the chosen channel takes", () => {
    verdict = CLEAN;
    renderForm(newActor("world"));
    fireEvent.mouseDown(screen.getByLabelText("Comes in"));
    expect(
      screen.getByRole("option", { name: "When the agent contacts them" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "When the agent says a word" }),
    ).toBeNull();
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
      screen.getByLabelText("Most lines in a call (optional)"),
    ).toHaveValue(3);
    expect(
      screen.queryByLabelText("Most turns in a call (optional)"),
    ).toBeNull();
  });

  it("offers the voice choices the cast lists, with Any for none", () => {
    verdict = CLEAN;
    renderForm(newActor("caller", "audio_mix"));
    fireEvent.mouseDown(screen.getByLabelText("Age"));
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
    const id = screen.getByLabelText("Id");
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
    fireEvent.change(screen.getByLabelText("Waits, in seconds (optional)"), {
      target: { value: "" },
    });
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
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Ops Lead" },
    });
    expect(screen.getByLabelText("Name")).toHaveAccessibleDescription(
      "Another actor is called Ops Lead.",
    );
    expect(screen.getByLabelText("Comes in")).toHaveAccessibleDescription(
      "Pick the agent turn they come in on.",
    );
    expect(screen.queryByTestId("actor-problems")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Save to draft" }),
    ).toBeDisabled();
  });

  it("opens a new actor clean, and asks for a name on its field once Save is tried", () => {
    verdict = {
      ...CLEAN,
      problems: [{ field: "name", message: "Give them a name." }],
    };
    const onSave = vi.fn();
    renderForm(newActor("world"), { onSave });
    expect(screen.queryByText("Give them a name.")).toBeNull();
    expect(screen.queryByTestId("actor-problems")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save to draft" }));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Name")).toHaveAccessibleDescription(
      "Give them a name.",
    );
    expect(
      screen.getByRole("button", { name: "Save to draft" }),
    ).toBeDisabled();
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
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Priya Raman" },
    });
    fireEvent.change(screen.getByLabelText("Id"), {
      target: { value: "a".repeat(70) },
    });

    expect(screen.getByLabelText("Id")).toHaveAccessibleDescription(
      "This must be at most 64 characters.",
    );
    expect(screen.queryByText(/Couldn't check this change/)).toBeNull();
  });

  it("says Discard keeps an uploaded document in the draft", () => {
    verdict = CLEAN;
    renderForm(typedActor({ id: "ops", name: "Ops" }));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Ops Lead" },
    });
    fireEvent.keyDown(screen.getByLabelText("Name"), { key: "Escape" });
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
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Ops Lead" },
    });
    expect(screen.getByTestId("actor-problems")).toHaveTextContent(
      "ops is in a cycle of parent links; break it.",
    );
    expect(
      screen.getByRole("button", { name: "Save to draft" }),
    ).toBeDisabled();
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
    expect(screen.getByLabelText("Kind (optional)")).toHaveTextContent(
      "An approver",
    );
    expect(screen.getByLabelText("Comes in")).toHaveTextContent(
      "When the agent contacts them",
    );
    expect(screen.getByLabelText("Waits, in seconds (optional)")).toHaveValue(
      45,
    );
  });

  it("shows only the trigger inputs the cast lists for the picked kind, and sends them typed", () => {
    verdict = CLEAN;
    const onSave = vi.fn();
    // A release where being contacted also takes a delay.
    const data = cast([], {
      trigger_fields: { ...TRIGGER_FIELDS, contacted: ["seconds"] },
    });
    renderForm(typedActor({ id: "ops", name: "Ops" }), { data, onSave });
    fireEvent.mouseDown(screen.getByLabelText("Comes in"));
    fireEvent.click(
      screen.getByRole("option", { name: "When the agent contacts them" }),
    );
    expect(screen.queryByLabelText("From the agent's turn")).toBeNull();
    fireEvent.change(screen.getByLabelText("Seconds after the start"), {
      target: { value: "12" },
    });
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
      screen.getByLabelText("How they join the conversation"),
    ).toHaveTextContent("Beside the caller");
  });
});
