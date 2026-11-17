// exp.rename: the leaderboard's Edit dialog saves only what changed, says
// what is wrong with a name in plain words (empty, too long, taken), keeps
// itself open with dev's words when a save is refused, and says so when a
// save could not be confirmed.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import RenameExperimentDialog from "../RenameExperimentDialog";

const state = vi.hoisted(() => ({
  nameTaken: undefined,
  checked: [],
  tagsDirty: false,
  tagsSave: null,
}));

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/common/Tags/ExperimentTagsField", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/Tags/useTags", async (importOriginal) => ({
  ...(await importOriginal()),
  useExperimentTagsDraft: () => ({
    ids: [],
    setIds: () => {},
    dirty: state.tagsDirty,
    tags: [],
    save: state.tagsSave,
    saving: false,
    error: "",
  }),
}));
vi.mock("src/api/develop/experiment-create", async (importOriginal) => ({
  ...(await importOriginal()),
  useExperimentNameCheck: (datasetId, name, { enabled = true } = {}) => {
    if (enabled && datasetId && name) state.checked.push(name);
    return { data: enabled ? state.nameTaken : undefined };
  },
}));
vi.mock("src/hooks/use-debounce", async (importOriginal) => ({
  ...(await importOriginal()),
  useDebounce: (value) => value,
}));

const TARGET = { id: "e1", name: "Prompt v1", description: "First try" };

function open(props = {}) {
  const onSave = props.onSave ?? vi.fn(() => Promise.resolve({}));
  const onClose = props.onClose ?? vi.fn();
  const view = render(
    <RenameExperimentDialog
      target={TARGET}
      datasetId="ds1"
      onClose={onClose}
      onSave={onSave}
      loading={props.loading ?? false}
    />,
  );
  return { ...view, onSave, onClose };
}

const nameField = () => screen.getByRole("textbox", { name: /^Name/ });
const descriptionField = () =>
  screen.getByRole("textbox", { name: "Description" });
const saveButton = () => screen.getByRole("button", { name: "Save" });
const type = (el, value) => fireEvent.change(el, { target: { value } });

beforeEach(() => {
  state.nameTaken = undefined;
  state.checked = [];
  state.tagsDirty = false;
  state.tagsSave = vi.fn(() => Promise.resolve());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Edit experiment: what Save sends", () => {
  it("sends only the new name, trimmed", async () => {
    const { onSave } = open();
    type(nameField(), "  Prompt v2  ");
    fireEvent.click(saveButton());
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith({ id: "e1", name: "Prompt v2" });
  });

  it("clearing the description sends the description alone", async () => {
    const { onSave } = open();
    type(descriptionField(), "");
    fireEvent.click(saveButton());
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith({ id: "e1", description: "" });
  });

  it("tags alone save the tags and close without a rename", async () => {
    state.tagsDirty = true;
    const { onSave, onClose } = open();
    fireEvent.click(saveButton());
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(state.tagsSave).toHaveBeenCalledTimes(1);
    expect(onSave).not.toHaveBeenCalled();
  });
});

describe("Edit experiment: names in plain words", () => {
  it("an empty name says what to do and Save is off", () => {
    open();
    type(nameField(), "   ");
    expect(screen.getByText("Enter an experiment name.")).toBeInTheDocument();
    expect(nameField()).toHaveAttribute("aria-invalid", "true");
    expect(saveButton()).toBeDisabled();
  });

  it("a name over 255 characters says the limit and is not cut short", () => {
    open();
    const long = "n".repeat(256);
    type(nameField(), long);
    expect(nameField()).toHaveValue(long);
    expect(
      screen.getByText("Use 255 characters or fewer for the experiment name."),
    ).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
    type(nameField(), "n".repeat(255));
    expect(saveButton()).toBeEnabled();
  });

  it("counts characters as dev does: 200 emoji are within the limit", () => {
    open();
    type(nameField(), "\u{1F600}".repeat(200));
    expect(
      screen.queryByText("Use 255 characters or fewer for the experiment name."),
    ).toBeNull();
    expect(saveButton()).toBeEnabled();
  });

  it("a description over 4000 characters says the limit", () => {
    open();
    type(descriptionField(), "d".repeat(4001));
    expect(
      screen.getByText("Use 4000 characters or fewer for the description."),
    ).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
  });

  it("a name another experiment in the dataset has is caught before Save", () => {
    state.nameTaken = false;
    open();
    type(nameField(), "Prompt v0");
    expect(state.checked).toContain("Prompt v0");
    expect(
      screen.getByText(
        "An experiment with this name already exists in this dataset. Choose another name.",
      ),
    ).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
  });

  it("the experiment's own name is never checked against itself", () => {
    state.nameTaken = false;
    open();
    type(descriptionField(), "Second try");
    expect(state.checked).toEqual([]);
    expect(saveButton()).toBeEnabled();
  });

  it("each field's message is its accessible description", () => {
    open();
    type(nameField(), "");
    expect(nameField()).toHaveAccessibleDescription(
      "Enter an experiment name.",
    );
    expect(nameField()).toBeRequired();
  });
});

describe("Edit experiment: a refused or unconfirmed save", () => {
  it("keeps the dialog open with dev's words next to the name", async () => {
    const onSave = vi.fn(() =>
      Promise.reject({
        status: false,
        statusCode: 400,
        attr: "name",
        detail:
          "name: An experiment with this name already exists in this dataset. Choose another name.",
        details: {
          name: [
            "An experiment with this name already exists in this dataset. Choose another name.",
          ],
        },
      }),
    );
    const { onClose } = open({ onSave });
    type(nameField(), "Prompt v0");
    await act(async () => {
      fireEvent.click(saveButton());
    });
    expect(
      await screen.findByText(
        "An experiment with this name already exists in this dataset. Choose another name.",
      ),
    ).toBeInTheDocument();
    expect(nameField()).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    // Editing the name clears dev's words.
    type(nameField(), "Prompt v3");
    expect(
      screen.queryByText(
        "An experiment with this name already exists in this dataset. Choose another name.",
      ),
    ).toBeNull();
  });

  it("403: shows the reason dev gives", async () => {
    const onSave = vi.fn(() =>
      Promise.reject({
        status: false,
        statusCode: 403,
        code: "permission_denied",
        detail:
          "You do not have permission to edit experiments in this workspace.",
      }),
    );
    open({ onSave });
    type(nameField(), "Prompt v2");
    await act(async () => {
      fireEvent.click(saveButton());
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You do not have permission to edit experiments in this workspace.",
    );
  });

  it("no answer: says the save could not be confirmed", async () => {
    const onSave = vi.fn(() =>
      Promise.reject({ message: "Network Error", transportCode: "ERR_NETWORK" }),
    );
    open({ onSave });
    type(nameField(), "Prompt v2");
    await act(async () => {
      fireEvent.click(saveButton());
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "We couldn't confirm the save. Check the experiment in the list before you try again.",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("after an unconfirmed save, its own new name is not called taken", async () => {
    const onSave = vi.fn(() =>
      Promise.reject({ message: "Network Error", transportCode: "ERR_NETWORK" }),
    );
    const view = open({ onSave });
    type(nameField(), "Prompt v2");
    await act(async () => {
      fireEvent.click(saveButton());
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "We couldn't confirm the save. Check the experiment in the list before you try again.",
    );
    // The save may have landed, so dev now says this experiment holds the
    // name. The dialog does not ask, and a retry stays possible.
    state.nameTaken = false;
    state.checked = [];
    view.rerender(
      <RenameExperimentDialog
        target={TARGET}
        datasetId="ds1"
        onClose={view.onClose}
        onSave={onSave}
        loading={false}
      />,
    );
    expect(state.checked).toEqual([]);
    expect(
      screen.queryByText(
        "An experiment with this name already exists in this dataset. Choose another name.",
      ),
    ).toBeNull();
    expect(saveButton()).toBeEnabled();
    // Editing the name again resumes the check.
    type(nameField(), "Prompt v3");
    expect(state.checked).toContain("Prompt v3");
  });

  it("while saving, the fields and Cancel wait", () => {
    open({ loading: true });
    expect(nameField()).toBeDisabled();
    expect(descriptionField()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });
});
