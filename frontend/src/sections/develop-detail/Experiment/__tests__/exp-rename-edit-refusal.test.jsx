// exp.rename: the Edit experiment drawer's Save (editSave.js runEditSave).
// The name / description save runs first and on its own; the run settings
// follow only when they changed; a refused name or description puts dev's
// words under that field (the Configuration step's FormTextFieldV2), not only
// in a toast; a failed settings save after a saved rename says the name is
// kept. Refusals use the src/utils/axios shape ({...body, statusCode}).
import React from "react";
import PropTypes from "prop-types";
import { useForm, useFormState } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import {
  renameErrorOf,
  renameErrorText,
  renameSuccessText,
} from "src/api/develop/experiment-metadata";
import {
  ownNamesOf,
  renameFieldErrors,
  runEditSave,
  settingsErrorText,
} from "../editSave";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const DUPLICATE =
  "An experiment with this name already exists in this dataset. Choose another name.";
const LONG = "Use 255 characters or fewer for the experiment name.";

const refused = (status, body) => ({
  ...body,
  statusCode: status,
  transportCode: "ERR_BAD_REQUEST",
});
const duplicateName = refused(400, {
  status: false,
  type: "validation_error",
  code: "invalid",
  attr: "name",
  detail: `name: ${DUPLICATE}`,
  details: { name: [DUPLICATE] },
});
const longName = refused(400, {
  status: false,
  type: "validation_error",
  code: "invalid",
  attr: "name",
  detail: `name: ${LONG}`,
  details: { name: [LONG] },
});
const answer = (result) => ({ data: { status: true, result } });

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("runEditSave: the order of an Edit save", () => {
  it("a rename alone saves the name and sends no run settings", async () => {
    const saveMetadata = vi.fn(async () => answer({ name: "Exp 2b" }));
    const saveSettings = vi.fn(async () => answer({}));
    const outcome = await runEditSave(
      { metadata: { name: "Exp 2b" }, config: null },
      { saveMetadata, saveSettings },
    );
    expect(saveMetadata).toHaveBeenCalledWith({ name: "Exp 2b" });
    expect(saveSettings).not.toHaveBeenCalled();
    expect(outcome.done).toEqual(["metadata"]);
    expect(outcome.failed).toBeNull();
    expect(renameSuccessText(outcome.renamed, { name: "Exp 2b" })).toBe(
      "Renamed to “Exp 2b”",
    );
  });

  it("a rename with changed runs saves the name first, then the run settings", async () => {
    const order = [];
    const saveMetadata = vi.fn(async () => {
      order.push("metadata");
      return answer({ name: "Exp 2b" });
    });
    const saveSettings = vi.fn(async () => {
      order.push("settings");
      return answer({ changes: { rerun: ["#2A"] } });
    });
    const config = { prompt_config: [{ run_id: "run-a" }] };
    const outcome = await runEditSave(
      { metadata: { name: "Exp 2b" }, config },
      { saveMetadata, saveSettings },
    );
    expect(order).toEqual(["metadata", "settings"]);
    expect(saveSettings).toHaveBeenCalledWith(config);
    expect(outcome.done).toEqual(["metadata", "settings"]);
    expect(outcome.failed).toBeNull();
    expect(outcome.saved.data.result.changes.rerun).toEqual(["#2A"]);
  });

  it("a refused name stops there: the run settings are never sent", async () => {
    const saveMetadata = vi.fn(() => Promise.reject(duplicateName));
    const saveSettings = vi.fn(async () => answer({}));
    const outcome = await runEditSave(
      { metadata: { name: "Exp 1" }, config: { prompt_config: [] } },
      { saveMetadata, saveSettings },
    );
    expect(saveSettings).not.toHaveBeenCalled();
    expect(outcome.done).toEqual([]);
    expect(outcome.failed).toEqual({ step: "metadata", error: duplicateName });
  });

  it("no name or description change: only the run settings, as before", async () => {
    const saveMetadata = vi.fn(async () => answer({}));
    const saveSettings = vi.fn(async () => answer({ changes: {} }));
    const config = { prompt_config: [] };
    const outcome = await runEditSave(
      { metadata: null, config },
      { saveMetadata, saveSettings },
    );
    expect(saveMetadata).not.toHaveBeenCalled();
    expect(saveSettings).toHaveBeenCalledWith(config);
    expect(outcome.done).toEqual(["settings"]);
  });

  it("a failed settings save after a saved rename keeps the name and says so", async () => {
    const settingsRefusal = refused(400, {
      status: false,
      result: "Choose a model for run A.",
    });
    const saveMetadata = vi.fn(async () => answer({ name: "Exp 2b" }));
    const saveSettings = vi.fn(() => Promise.reject(settingsRefusal));
    const plan = {
      metadata: { name: "Exp 2b" },
      config: { prompt_config: [] },
    };
    const outcome = await runEditSave(plan, { saveMetadata, saveSettings });
    expect(outcome.done).toEqual(["metadata"]);
    expect(outcome.failed).toEqual({
      step: "settings",
      error: settingsRefusal,
    });
    const kept = renameSuccessText(outcome.renamed, plan.metadata);
    expect(settingsErrorText(outcome.failed.error, kept)).toBe(
      "Renamed to “Exp 2b”. The run settings were not saved: Choose a model for run A.",
    );
    // Nothing was renamed: the settings error reads as before.
    expect(settingsErrorText(settingsRefusal)).toBe(
      "Choose a model for run A.",
    );
    expect(settingsErrorText({ message: "" })).toBe(
      "Could not save the experiment.",
    );
  });
});

describe("ownNamesOf: names the Edit name check never calls taken", () => {
  it("the name it opened with, else the one a save already saved", () => {
    expect(ownNamesOf({ opened: "Exp 2" })).toEqual(["Exp 2"]);
    expect(ownNamesOf({ opened: "Exp 2", saved: " Exp 2b " })).toEqual([
      "Exp 2b",
    ]);
  });

  it("a name sent without an answer may be its own too", () => {
    expect(
      ownNamesOf({ opened: "Exp 2", saved: undefined, unconfirmed: "Exp 2c" }),
    ).toEqual(["Exp 2", "Exp 2c"]);
    expect(ownNamesOf()).toEqual([]);
  });
});

const SCHEMA = z.object({
  name: z.string().trim().min(1, "Enter an experiment name."),
  description: z.string(),
});

// The drawer's form as the Configuration step holds it, with the drawer's
// Save: runEditSave, then a refused field's words on that field
// (RunExperiment marks the name field when errors.name is dev's).
function EditNameStep({ saveMetadata, onToast }) {
  const { control, handleSubmit, setError } = useForm({
    defaultValues: { name: "Exp 2", description: "First try" },
    resolver: zodResolver(SCHEMA),
  });
  const { errors } = useFormState({ control });
  const save = handleSubmit(async (values) => {
    const outcome = await runEditSave(
      { metadata: { name: values.name }, config: null },
      { saveMetadata, saveSettings: () => Promise.resolve(null) },
    );
    if (outcome.failed?.step !== "metadata") return;
    const failure = renameErrorOf(outcome.failed.error);
    if (!renameFieldErrors(failure, setError))
      onToast(renameErrorText(failure));
  });
  return (
    <form onSubmit={save}>
      <FormTextFieldV2
        id="experiment-name"
        control={control}
        fieldName="name"
        label="Experiment name"
        size="small"
        error={errors?.name?.type === "server"}
      />
      <FormTextFieldV2
        id="experiment-description"
        control={control}
        fieldName="description"
        label="Description (optional)"
        size="small"
      />
      <button type="submit">Save</button>
    </form>
  );
}

EditNameStep.propTypes = {
  saveMetadata: PropTypes.func.isRequired,
  onToast: PropTypes.func.isRequired,
};

function openEdit(saveMetadata) {
  const onToast = vi.fn();
  render(<EditNameStep saveMetadata={saveMetadata} onToast={onToast} />);
  return { onToast };
}

const nameField = () =>
  screen.getByRole("textbox", { name: "Experiment name" });

async function saveAs(name) {
  fireEvent.change(nameField(), { target: { value: name } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
  });
}

describe("Edit experiment drawer: a refused rename is said under the name", () => {
  it("a name another experiment in the dataset has: dev's words under Name, no toast", async () => {
    const saveMetadata = vi.fn(() => Promise.reject(duplicateName));
    const { onToast } = openEdit(saveMetadata);
    await saveAs("Exp 1");
    expect(await screen.findByText(DUPLICATE)).toBeInTheDocument();
    expect(saveMetadata).toHaveBeenCalledWith({ name: "Exp 1" });
    expect(nameField()).toHaveAttribute("aria-invalid", "true");
    expect(onToast).not.toHaveBeenCalled();
    // Editing the name re-validates the field and clears dev's words.
    fireEvent.change(nameField(), { target: { value: "Exp 3" } });
    await waitFor(() => expect(screen.queryByText(DUPLICATE)).toBeNull());
    expect(nameField()).toHaveAttribute("aria-invalid", "false");
  });

  it("a name over dev's limit: the limit under Name", async () => {
    const saveMetadata = vi.fn(() => Promise.reject(longName));
    const { onToast } = openEdit(saveMetadata);
    await saveAs("n".repeat(256));
    expect(await screen.findByText(LONG)).toBeInTheDocument();
    expect(nameField()).toHaveAttribute("aria-invalid", "true");
    expect(onToast).not.toHaveBeenCalled();
  });

  it("a refusal that is not about a field stays a toast", async () => {
    const saveMetadata = vi.fn(() =>
      Promise.reject(
        refused(403, {
          status: false,
          code: "permission_denied",
          detail:
            "You do not have permission to edit experiments in this workspace.",
        }),
      ),
    );
    const { onToast } = openEdit(saveMetadata);
    await saveAs("Exp 2b");
    await waitFor(() =>
      expect(onToast).toHaveBeenCalledWith(
        "You do not have permission to edit experiments in this workspace.",
      ),
    );
    expect(nameField()).toHaveAttribute("aria-invalid", "false");
  });
});
