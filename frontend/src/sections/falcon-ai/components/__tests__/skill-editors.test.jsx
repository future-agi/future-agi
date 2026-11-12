import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from "src/utils/test-utils";

import SkillEditorDialog from "../SkillEditorDialog";
import CustomizePanel from "../CustomizePanel";

// Both skill editors reach the server only through these calls. The Customize
// editor imports createSkill/updateSkill lazily; vi.mock covers that too.
const mocks = vi.hoisted(() => ({
  listSkills: vi.fn(),
  getSkill: vi.fn(),
  createSkill: vi.fn(),
  updateSkill: vi.fn(),
  deleteSkill: vi.fn(),
  fetchConnectors: vi.fn(),
  getConnector: vi.fn(),
  deleteConnector: vi.fn(),
  updateConnectorTools: vi.fn(),
  discoverConnectorTools: vi.fn(),
  authenticateConnector: vi.fn(),
  falconAIQueryKeys: {
    connector: (id) => ["falcon-ai", "connector", id],
  },
}));

vi.mock("../../hooks/useFalconAPI", () => mocks);

// Name, Description and Instructions share placeholders in both editors. The
// Customize ones are kept as they were because the browser skills smoke types
// into them. The trigger phrase example differs on purpose: the dialog shows
// plain words, because a trigger phrase is not a slash command.
const NAME = "e.g. Build a Dataset";
const DESCRIPTION = "What does this skill do?";
const INSTRUCTIONS =
  "Write detailed instructions for how Falcon should behave when this skill is active...";
const PHRASE = {
  dialog: "e.g. weekly summary",
  customize: "e.g. /build-dataset",
};

const VALID = {
  name: "Weekly summary",
  description: "Sums up my week",
  instructions: "Summarise last week's evaluations in five short bullets.",
  phrase: "weekly summary",
};

const EXISTING = {
  id: "skill-1",
  name: "Bug triage",
  description: "",
  // A registered backend default, so the list row renders a real icon.
  icon: "mdi:bug-outline",
  instructions: "Group new errors by their likely cause.",
  trigger_phrases: ["triage errors"],
};

// "dialog": the Create Skill window under the empty chat (SkillPicker) and in
// the header Skills menu. "customize": the editor inside Chat > Customize.
const SURFACES = ["dialog", "customize"];

const renderCustomize = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <CustomizePanel />
    </QueryClientProvider>,
  );
};

const openCreateEditor = async (surface) => {
  if (surface === "dialog") {
    render(
      <SkillEditorDialog
        open
        skill={null}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    return;
  }
  renderCustomize();
  // Let the (empty) skills list settle before opening the editor.
  await screen.findByText("No skills yet");
  fireEvent.click(screen.getByTitle("Create skill"));
};

const saveButton = (surface) =>
  screen.getByRole("button", {
    name: surface === "dialog" ? "Create" : "Save",
  });

const type = (placeholder, value) =>
  fireEvent.change(screen.getByPlaceholderText(placeholder), {
    target: { value },
  });

const fill = ({ name, description, instructions }) => {
  type(NAME, name);
  type(DESCRIPTION, description);
  type(INSTRUCTIONS, instructions);
};

const addPhrase = (surface, value) => {
  type(PHRASE[surface], value);
  fireEvent.keyDown(screen.getByPlaceholderText(PHRASE[surface]), {
    key: "Enter",
  });
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listSkills.mockResolvedValue({ results: [] });
  mocks.fetchConnectors.mockResolvedValue({ results: [] });
  mocks.createSkill.mockResolvedValue({ result: { id: "new-skill" } });
  mocks.updateSkill.mockResolvedValue({ result: {} });
});

describe("skill editors ask for the same fields", () => {
  // The Customize editor never had an icon field; the Create Skill window has
  // one again (see "the Create Skill window has a working Icon field").
  it("customize: no icon code to type", async () => {
    await openCreateEditor("customize");

    expect(screen.queryByRole("textbox", { name: /icon/i })).toBeNull();
    expect(screen.queryByText(/icon name/i)).toBeNull();
    expect(screen.queryByPlaceholderText(/solar:|mdi:/)).toBeNull();
  });

  it.each(SURFACES)(
    "%s: name, description, instructions and trigger phrases",
    async (surface) => {
      await openCreateEditor(surface);

      for (const placeholder of [
        NAME,
        DESCRIPTION,
        INSTRUCTIONS,
        PHRASE[surface],
      ]) {
        expect(screen.getByPlaceholderText(placeholder)).toBeInTheDocument();
      }
    },
  );

  it("dialog: those four fields, the Icon field, and nothing else", async () => {
    await openCreateEditor("dialog");

    expect(
      within(screen.getByRole("dialog")).getAllByRole("textbox"),
    ).toHaveLength(5);
  });

  // Only the skill's name makes a slash command (/<slug>); an example like
  // "/build-dataset" would teach people to type a command that runs the
  // built-in Build a Dataset skill, or no skill at all.
  it("dialog: trigger phrase example is not a slash command", async () => {
    await openCreateEditor("dialog");

    const phrase = screen.getByRole("textbox", { name: /trigger phrases/i });
    expect(phrase.getAttribute("placeholder")).not.toContain("/");
  });
});

// The server stores a skill's icon (Skill.icon) and the skill lists, picker
// and slash menu show it, so the Create Skill window lets people set it.
describe("the Create Skill window has a working Icon field", () => {
  const iconField = () => screen.getByRole("textbox", { name: "Icon" });
  const ICON_HINT =
    "Icon name, e.g. mdi:bug-outline or mdi:star. Leave empty for the default star.";

  it("dialog: an Icon field with its hint, starting on the default icon", async () => {
    await openCreateEditor("dialog");

    expect(iconField()).toHaveValue("solar:star-linear");
    expect(iconField()).toBeEnabled();
    expect(screen.getByText(ICON_HINT)).toBeInTheDocument();
  });

  // Icons are bundled, never fetched, so a name the app doesn't ship shows
  // the missing-icon box. The hint's examples must draw a real icon here,
  // and in every skill list, without depending on pages opened before.
  it("dialog: the names the hint gives preview as real icons", async () => {
    await openCreateEditor("dialog");
    const preview = () =>
      iconField().closest(".MuiInputBase-root").querySelector("svg.iconify");

    for (const name of ["mdi:bug-outline", "mdi:star"]) {
      expect(ICON_HINT).toContain(name);
      fireEvent.change(iconField(), { target: { value: name } });
      expect(preview(), name).toHaveClass("iconify--mdi");
      expect(preview(), name).not.toHaveAttribute("data-missing-icon");
    }
  });

  it("dialog: the preview follows what is typed", async () => {
    await openCreateEditor("dialog");

    fireEvent.change(iconField(), { target: { value: "mdi:no-such-icon" } });
    // An icon name the app does not ship shows the visible missing glyph.
    expect(
      within(screen.getByRole("dialog")).getByRole("img", {
        name: "Missing offline icon: mdi:no-such-icon",
      }),
    ).toBeInTheDocument();

    fireEvent.change(iconField(), { target: { value: "" } });
    expect(
      within(screen.getByRole("dialog")).queryByRole("img", {
        name: "Missing offline icon: mdi:no-such-icon",
      }),
    ).toBeNull();
  });

  it("dialog: saves the icon that was typed", async () => {
    await openCreateEditor("dialog");
    fill(VALID);
    fireEvent.change(iconField(), { target: { value: "mdi:bug-outline" } });
    addPhrase("dialog", VALID.phrase);

    fireEvent.click(saveButton("dialog"));

    await waitFor(() => expect(mocks.createSkill).toHaveBeenCalledTimes(1));
    expect(mocks.createSkill).toHaveBeenCalledWith({
      name: VALID.name,
      description: VALID.description,
      icon: "mdi:bug-outline",
      instructions: VALID.instructions,
      trigger_phrases: [VALID.phrase],
    });
  });

  it("dialog: an Icon field left empty saves the default icon", async () => {
    await openCreateEditor("dialog");
    fill(VALID);
    fireEvent.change(iconField(), { target: { value: "" } });
    addPhrase("dialog", VALID.phrase);

    fireEvent.click(saveButton("dialog"));

    await waitFor(() => expect(mocks.createSkill).toHaveBeenCalledTimes(1));
    expect(mocks.createSkill).toHaveBeenCalledWith(
      expect.objectContaining({ icon: "solar:star-linear" }),
    );
  });

  it("dialog: editing shows the skill's own icon and saves a new one", async () => {
    render(
      <SkillEditorDialog
        open
        skill={EXISTING}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    expect(iconField()).toHaveValue(EXISTING.icon);

    fireEvent.change(iconField(), { target: { value: "mdi:star" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mocks.updateSkill).toHaveBeenCalledTimes(1));
    expect(mocks.updateSkill).toHaveBeenCalledWith(
      EXISTING.id,
      expect.objectContaining({ icon: "mdi:star" }),
    );
  });
});

describe("skill editors check the same rules", () => {
  it.each(SURFACES)(
    "%s: says what is missing instead of a dead Save button",
    async (surface) => {
      await openCreateEditor(surface);

      fireEvent.click(saveButton(surface));

      expect(await screen.findByText("Name is required")).toBeInTheDocument();
      expect(mocks.createSkill).not.toHaveBeenCalled();
    },
  );

  it.each(SURFACES)("%s: refuses a one-letter name", async (surface) => {
    await openCreateEditor(surface);
    fill({ ...VALID, name: "A" });
    addPhrase(surface, VALID.phrase);

    fireEvent.click(saveButton(surface));

    expect(
      await screen.findByText("Name must be at least 2 characters"),
    ).toBeInTheDocument();
    expect(mocks.createSkill).not.toHaveBeenCalled();
  });

  it.each(SURFACES)(
    "%s: refuses instructions under 10 characters",
    async (surface) => {
      await openCreateEditor(surface);
      fill({ ...VALID, instructions: "Be brief" });
      addPhrase(surface, VALID.phrase);

      fireEvent.click(saveButton(surface));

      expect(
        await screen.findByText(
          "Instructions should be at least 10 characters",
        ),
      ).toBeInTheDocument();
      expect(mocks.createSkill).not.toHaveBeenCalled();
    },
  );

  it.each(SURFACES)(
    "%s: asks for a trigger phrase in plain words",
    async (surface) => {
      await openCreateEditor(surface);
      fill(VALID);

      fireEvent.click(saveButton(surface));

      expect(
        await screen.findByText("Add at least one trigger phrase"),
      ).toBeInTheDocument();
      expect(mocks.createSkill).not.toHaveBeenCalled();
    },
  );

  it.each(SURFACES)(
    "%s: keeps a trigger phrase typed without pressing Enter",
    async (surface) => {
      await openCreateEditor(surface);
      fill(VALID);
      type(PHRASE[surface], VALID.phrase);

      fireEvent.click(saveButton(surface));

      await waitFor(() => expect(mocks.createSkill).toHaveBeenCalledTimes(1));
      expect(mocks.createSkill).toHaveBeenCalledWith(
        expect.objectContaining({ trigger_phrases: [VALID.phrase] }),
      );
    },
  );
});

describe("the saved skill format is unchanged", () => {
  it.each(SURFACES)(
    "%s: sends the same five fields, with the default icon",
    async (surface) => {
      await openCreateEditor(surface);
      fill(VALID);
      addPhrase(surface, VALID.phrase);

      fireEvent.click(saveButton(surface));

      await waitFor(() => expect(mocks.createSkill).toHaveBeenCalledTimes(1));
      expect(mocks.createSkill).toHaveBeenCalledWith({
        name: VALID.name,
        description: VALID.description,
        icon: "solar:star-linear",
        instructions: VALID.instructions,
        trigger_phrases: [VALID.phrase],
      });
    },
  );

  const EDITED = {
    name: EXISTING.name,
    description: "Sorts new errors",
    icon: EXISTING.icon,
    instructions: EXISTING.instructions,
    trigger_phrases: EXISTING.trigger_phrases,
  };

  it("dialog: editing keeps the skill's own icon", async () => {
    render(
      <SkillEditorDialog
        open
        skill={EXISTING}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByRole("textbox", { name: /^description/i }), {
      target: { value: EDITED.description },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mocks.updateSkill).toHaveBeenCalledTimes(1));
    expect(mocks.updateSkill).toHaveBeenCalledWith(EXISTING.id, EDITED);
  });

  it("customize: editing keeps the skill's own icon", async () => {
    // The list row is a summary without instructions; the detail has them.
    const listRow = {
      id: EXISTING.id,
      name: EXISTING.name,
      description: EXISTING.description,
      icon: EXISTING.icon,
      trigger_phrases: EXISTING.trigger_phrases,
    };
    mocks.listSkills.mockResolvedValue({ results: [listRow] });
    mocks.getSkill.mockResolvedValue({ result: EXISTING });
    renderCustomize();

    fireEvent.click(await screen.findByText(EXISTING.name));
    await screen.findByText(EXISTING.instructions);
    fireEvent.click(screen.getByText("Edit skill"));
    type(DESCRIPTION, EDITED.description);

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mocks.updateSkill).toHaveBeenCalledTimes(1));
    expect(mocks.updateSkill).toHaveBeenCalledWith(EXISTING.id, EDITED);
  });
});
