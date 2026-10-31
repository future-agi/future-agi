import PropTypes from "prop-types";
import {
  describe,
  it,
  expect,
  vi,
  beforeEach,
  afterEach,
  afterAll,
} from "vitest";
import {
  configure,
  renderWithRouter,
  screen,
  fireEvent,
  within,
} from "src/utils/test-utils";

import { subscribeComposerScaffold } from "../../../buildEnvironment/console/composerScaffoldBus";
import { UI_ONLY_SAVED } from "../../gaps/NeedsInputButton";
import ScenarioEditor from "../ScenarioEditor";

// The persona drawers load lazily and the drawer is heavy, so give async
// queries and each test headroom under full-suite parallelism.
configure({ asyncUtilTimeout: 5000 });
afterAll(() => configure({ asyncUtilTimeout: 1000 }));
vi.setConfig({ testTimeout: 20000 });

vi.mock("notistack", async () => {
  const actual = await vi.importActual("notistack");
  return { ...actual, enqueueSnackbar: vi.fn() };
});
const { enqueueSnackbar } = await import("notistack");

// The product's persona library picker and create drawer, stubbed so the test
// can pick a persona and read what the editor passes them.
const LIBRARY_PERSONA = {
  id: "lib-1",
  name: "Priya",
  gender: ["female"],
  ageGroup: ["25-32"],
  accent: ["Indian"],
  communicationStyle: ["Formal"],
  personality: ["Patient"],
  profession: ["Teacher"],
  location: ["India"],
  languages: ["English", "Hindi"],
};

function PersonaDrawerStub({ open, onAddPersonas, onClose }) {
  if (!open) return null;
  return (
    <div role="dialog" aria-label="Persona library">
      <button type="button" onClick={() => onAddPersonas([LIBRARY_PERSONA])}>
        Pick Priya
      </button>
      <button type="button" onClick={onClose}>
        Close library
      </button>
    </div>
  );
}
PersonaDrawerStub.propTypes = {
  open: PropTypes.bool,
  onAddPersonas: PropTypes.func,
  onClose: PropTypes.func,
};
vi.mock("src/sections/persona/PersonaDrawer", () => ({
  default: (props) => <PersonaDrawerStub {...props} />,
}));

const createDrawerProps = vi.fn();
function CreateDrawerStub(props) {
  createDrawerProps(props);
  if (!props.open) return null;
  return (
    <div role="dialog" aria-label="Create persona">
      <span>type:{String(props.personaCreateEditType)}</span>
      <button type="button" onClick={() => props.updatePersonaType?.("voice")}>
        Choose voice
      </button>
    </div>
  );
}
CreateDrawerStub.propTypes = {
  open: PropTypes.bool,
  personaCreateEditType: PropTypes.string,
  updatePersonaType: PropTypes.func,
};
vi.mock(
  "src/sections/persona/PersonaCreateEdit/PersonaCreateEditDrawer",
  () => ({ default: (props) => <CreateDrawerStub {...props} /> }),
);

// The server's editing contract, as the sample suite serves it.
const EDITING = {
  editable_fields: ["background_noise", "max_turns", "tests"],
  persona_fields: [
    "accent",
    "communication_style",
    "keywords",
    "languages",
    "location",
    "occupation",
    "personality",
  ],
  rework_fields: [
    "accent",
    "background_noise",
    "communication_style",
    "keywords",
    "languages",
    "location",
    "max_turns",
    "occupation",
    "personality",
  ],
};

const ROW = {
  id: "r1",
  name: "booking_with_destination_change",
  useCase: "Geocode spoken addresses",
  conversationBranch: "Caller changes the dropoff mid-call",
  situation: "You want a ride from 200 Market Street to Union Square.",
  expected: "Passes when the agent confirms the updated destination.",
  subTasks: ["confirms_pickup_and_dropoff", "selects_requested_product"],
  maxTurns: 10,
  backgroundNoise: "street",
  keywords: ["address_correction"],
  _raw: {
    persona: {
      name: "Marcus",
      age_group: "32-40",
      gender: "male",
      accent: "American",
      communication_style: "Casual and friendly",
      personality: "Confident",
      occupation: "",
      location: "United States",
      languages: ["English"],
    },
  },
};

const renderEditor = (props = {}) => {
  const onSave = vi.fn();
  const onClose = vi.fn();
  renderWithRouter(
    <ScenarioEditor
      open
      onClose={onClose}
      onSave={onSave}
      row={ROW}
      scenarioEditing={EDITING}
      noiseOptions={["street", "quiet line"]}
      env={{ id: "job-1", surface: "voice" }}
      {...props}
    />,
  );
  return { onSave, onClose };
};

const saveButton = () => screen.getByRole("button", { name: "Save scenario" });

beforeEach(() => {
  enqueueSnackbar.mockClear();
  createDrawerProps.mockClear();
});

describe("ScenarioEditor: use a library persona", () => {
  it("fills the writable persona fields from the pick and saves them as one set_persona", async () => {
    const { onSave } = renderEditor();
    expect(saveButton()).toBeDisabled();

    fireEvent.click(
      screen.getByRole("button", { name: "Use a library persona" }),
    );
    fireEvent.click(
      within(
        await screen.findByRole("dialog", { name: "Persona library" }),
      ).getByRole("button", { name: "Pick Priya" }),
    );

    expect(screen.getByText(/Filled from Priya/)).toBeInTheDocument();
    expect(
      screen.queryByRole("dialog", { name: "Persona library" }),
    ).not.toBeInTheDocument();
    // Name, age group and gender are not writable, so the pick leaves them.
    expect(
      screen.getAllByLabelText("Name", { selector: "input" })[1],
    ).toHaveValue("Marcus");

    expect(saveButton()).toBeEnabled();
    fireEvent.click(saveButton());
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toEqual({
      rework: true,
      changes: [
        {
          op: "set_persona",
          scenario: ROW.name,
          persona: {
            accent: "Indian",
            communication_style: "Formal",
            personality: "Patient",
            occupation: "Teacher",
            location: "India",
            languages: ["English", "Hindi"],
          },
        },
      ],
    });
  });

  it("only fills the persona fields the server names as writable", async () => {
    const { onSave } = renderEditor({
      scenarioEditing: { ...EDITING, persona_fields: ["accent"] },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Use a library persona" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Pick Priya" }));
    fireEvent.click(saveButton());
    expect(onSave.mock.calls[0][0].changes).toEqual([
      { op: "set_persona", scenario: ROW.name, persona: { accent: "Indian" } },
    ]);
  });

  it("hides the library pick when no persona field is writable", () => {
    renderEditor({ scenarioEditing: { ...EDITING, persona_fields: [] } });
    expect(
      screen.queryByRole("button", { name: "Use a library persona" }),
    ).not.toBeInTheDocument();
  });
});

describe("ScenarioEditor: save to library", () => {
  it("opens the product's create persona drawer for the environment's type, in create mode", async () => {
    renderEditor();
    expect(
      screen.queryByRole("dialog", { name: "Create persona" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save to library" }));
    expect(
      await screen.findByRole("dialog", { name: "Create persona" }),
    ).toBeInTheDocument();
    expect(screen.getByText("type:voice")).toBeInTheDocument();
    // editPersona flips the form to a PATCH of an existing persona, so it is
    // never passed.
    const last = createDrawerProps.mock.calls.at(-1)[0];
    expect(last.editPersona).toBeUndefined();
  });

  it("maps a chat environment to the text persona type", async () => {
    renderEditor({ env: { id: "job-1", surface: "chat" } });
    fireEvent.click(screen.getByRole("button", { name: "Save to library" }));
    expect(await screen.findByText("type:text")).toBeInTheDocument();
  });

  it("lets the drawer ask for the type when the environment has none", async () => {
    renderEditor({ env: undefined });
    fireEvent.click(screen.getByRole("button", { name: "Save to library" }));
    expect(await screen.findByText("type:null")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Choose voice" }));
    expect(screen.getByText("type:voice")).toBeInTheDocument();
  });
});

describe("ScenarioEditor: send to builder", () => {
  let received;
  let unsubscribe;
  beforeEach(() => {
    received = vi.fn();
    unsubscribe = subscribeComposerScaffold(received);
  });
  afterEach(() => unsubscribe());

  it("puts a block describing the scenario into the builder composer and closes", () => {
    const { onSave, onClose } = renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "Send to builder" }));
    expect(received).toHaveBeenCalledTimes(1);
    const text = received.mock.calls[0][0];
    expect(text).toContain(ROW.name);
    expect(text).toContain(ROW.situation);
    expect(text).toContain(ROW.expected);
    expect(text).toContain("confirms_pickup_and_dropoff");
    expect(text).toContain("Marcus");
    expect(onClose).toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("sends a locked field's change request with the scenario it is about", () => {
    const { onClose } = renderEditor();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Ask the builder to change what the caller wants",
      }),
    );
    const text = received.mock.calls[0][0];
    expect(text).toMatch(/change what the caller wants/i);
    expect(text).toContain(ROW.name);
    expect(onClose).toHaveBeenCalled();
  });
});

describe("ScenarioEditor: sub-goals (UI only)", () => {
  it("edits sub-goals locally, flags them UI only, and leaves them out of the save", () => {
    const { onSave } = renderEditor();
    const section = screen.getByRole("region", { name: "Sub-goals" });
    expect(within(section).getByLabelText("UI only")).toBeInTheDocument();
    expect(
      within(section).getByText("confirms_pickup_and_dropoff"),
    ).toBeInTheDocument();

    fireEvent.change(within(section).getByPlaceholderText(/Add a sub-goal/), {
      target: { value: "Quotes the regular fare" },
    });
    fireEvent.click(within(section).getByRole("button", { name: "Add" }));
    expect(
      within(section).getByText("Quotes the regular fare"),
    ).toBeInTheDocument();
    expect(enqueueSnackbar).toHaveBeenCalledWith(UI_ONLY_SAVED, {
      variant: "info",
    });
    // A sub-goal edit alone has nothing to save.
    expect(saveButton()).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Passes when"), {
      target: { value: "Passes when it quotes the fare." },
    });
    fireEvent.click(saveButton());
    expect(onSave.mock.calls[0][0]).toEqual({
      rework: false,
      changes: [
        {
          op: "set_field",
          scenario: ROW.name,
          field: "tests",
          value: "Passes when it quotes the fare.",
        },
      ],
    });
  });

  it("keeps at least two sub-goals", () => {
    renderEditor();
    const section = screen.getByRole("region", { name: "Sub-goals" });
    const removes = within(section).getAllByRole("button", {
      name: /^Remove /,
    });
    expect(removes[0]).toBeDisabled();
  });
});

describe("ScenarioEditor: must-pass (UI only)", () => {
  it("toggles locally, flags it UI only, and saves nothing", () => {
    renderEditor();
    const section = screen.getByRole("region", { name: "Must-pass" });
    expect(within(section).getByLabelText("UI only")).toBeInTheDocument();
    fireEvent.click(
      within(section).getByRole("checkbox", { name: "Must-pass" }),
    );
    expect(enqueueSnackbar).toHaveBeenCalledWith(UI_ONLY_SAVED, {
      variant: "info",
    });
    expect(saveButton()).toBeDisabled();
  });
});

describe("ScenarioEditor: version preview (UI only)", () => {
  it("states what the edit does to past results before Save", () => {
    renderEditor();
    expect(
      screen.queryByRole("region", { name: "Version preview" }),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Passes when"), {
      target: { value: "Passes when it quotes the fare." },
    });
    const preview = screen.getByRole("region", { name: "Version preview" });
    expect(within(preview).getByLabelText("UI only")).toBeInTheDocument();
    expect(
      within(preview).getByText("Past runs are re-graded"),
    ).toBeInTheDocument();

    fireEvent.click(
      within(preview).getByRole("button", {
        name: "It tests something else now",
      }),
    );
    expect(
      within(preview).getByText("Past results need a re-run"),
    ).toBeInTheDocument();
  });

  it("calls a re-proving edit a re-run", async () => {
    renderEditor();
    fireEvent.click(
      screen.getByRole("button", { name: "Use a library persona" }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Pick Priya" }));
    const preview = screen.getByRole("region", { name: "Version preview" });
    expect(
      within(preview).getByText("Past results need a re-run"),
    ).toBeInTheDocument();
    expect(
      within(preview).queryByRole("button", {
        name: "It tests something else now",
      }),
    ).not.toBeInTheDocument();
  });

  it("does not show for sub-goal edits, which are not saved", () => {
    renderEditor();
    const section = screen.getByRole("region", { name: "Sub-goals" });
    fireEvent.change(within(section).getByPlaceholderText(/Add a sub-goal/), {
      target: { value: "Quotes the regular fare" },
    });
    fireEvent.click(within(section).getByRole("button", { name: "Add" }));
    expect(
      screen.queryByRole("region", { name: "Version preview" }),
    ).not.toBeInTheDocument();
  });
});

describe("ScenarioEditor: server gates kept", () => {
  it("keeps the proved fields read-only and saves only the changed editable field", () => {
    const { onSave } = renderEditor();
    const [scenarioName] = screen.getAllByLabelText("Name", {
      selector: "input",
    });
    expect(scenarioName).toHaveValue(ROW.name);
    expect(scenarioName).toBeDisabled();
    expect(screen.getByLabelText("Use case")).toBeDisabled();
    expect(screen.getByLabelText("Branch")).toBeDisabled();
    // Keywords are a persona field on the server but not an editable field, so
    // the editor keeps them read-only, as before.
    expect(screen.getByLabelText("Keywords")).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Passes when"), {
      target: { value: "Changed." },
    });
    fireEvent.click(saveButton());
    expect(onSave.mock.calls[0][0].changes).toEqual([
      {
        op: "set_field",
        scenario: ROW.name,
        field: "tests",
        value: "Changed.",
      },
    ]);
  });
});
