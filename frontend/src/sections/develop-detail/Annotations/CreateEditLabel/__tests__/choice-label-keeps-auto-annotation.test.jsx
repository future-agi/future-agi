import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import CreateEditLabel from "../CreateEditLabel";

// A choice label made in the dataset label form can annotate rows on its own:
// the API stores its rule prompt, whether it auto-annotates, its strategy and
// the columns it reads in its settings. The project label form changes only a
// choice label's choices and Multi Choice, so Update sends the rest back as
// they were stored. A stored choice label without them, and a new choice
// label, are saved with today's defaults.
const PROJECT_ID = "project-labels";

const NEW_NAME = "Renamed label";

// A choice label as the API returns it, with auto-annotation on.
const STORED_AUTO_CHOICE = {
  id: "label-auto-choice",
  name: "Tone",
  type: "categorical",
  description: "",
  settings: {
    rule_prompt: "Is the reply in {{column-output}} calm or tense?",
    multi_choice: false,
    few_shot: [],
    options: [{ label: "Calm" }, { label: "Tense" }],
    strategy: "Rag",
    query: "Query for RAG",
    query_col: "",
    auto_annotate: true,
    inputs: ["column-output"],
  },
};

// A choice label as the API can return it with no rule prompt,
// auto-annotation or strategy stored.
const STORED_PLAIN_CHOICE = {
  id: "label-plain-choice",
  name: "Mood",
  type: "categorical",
  description: "",
  settings: {
    options: [{ label: "Happy" }, { label: "Sad" }],
    multi_choice: false,
  },
};

let queryClient;

const saveButton = () =>
  screen.getByRole("button", { name: "save-edited-annotation" });

const nameField = () => screen.getByPlaceholderText("Enter label name");

const choiceField = () =>
  screen.getByPlaceholderText("Type a choice and press enter");

// The Multi Choice box in the form, not the choice boxes in the preview.
const multiChoiceBox = () =>
  within(
    screen.getByText("Multi Choice").closest(".MuiFormControlLabel-root"),
  ).getByRole("checkbox");

// Renders the real form; a null label opens it to create a new one.
const openLabelForm = (label) => {
  const put = vi
    .spyOn(axios, "put")
    .mockResolvedValue({ data: { result: label } });
  const post = vi
    .spyOn(axios, "post")
    .mockResolvedValue({ data: { result: label } });
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <SnackbarProvider>
        <CreateEditLabel
          open
          onClose={vi.fn()}
          projectId={PROJECT_ID}
          onSuccess={vi.fn()}
          editData={label}
        />
      </SnackbarProvider>
    </QueryClientProvider>,
  );
  return { put, post };
};

const typeName = async (name) => {
  await act(async () => {
    fireEvent.change(nameField(), { target: { value: name } });
  });
};

// Types a choice and presses Enter, as a person adds one.
const addChoice = async (choice) => {
  await act(async () => {
    fireEvent.change(choiceField(), { target: { value: choice } });
  });
  await act(async () => {
    fireEvent.keyDown(choiceField(), { key: "Enter" });
  });
};

// Picks an annotation type in a new label's Annotation Type select.
const chooseType = async (name) => {
  await act(async () => {
    fireEvent.click(screen.getByPlaceholderText("Select Option"));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("menuitem", { name }));
  });
};

// Presses the real Update or Save button once it is enabled.
const pressSave = async () => {
  await waitFor(() => expect(saveButton()).toBeEnabled());
  await act(async () => {
    fireEvent.click(saveButton());
  });
};

const expectUpdated = async (put, label, name, settings) => {
  await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
  expect(put).toHaveBeenCalledWith(endpoints.project.updateLabel(label.id), {
    name,
    type: label.type,
    description: "",
    project: PROJECT_ID,
    settings,
  });
};

afterEach(() => {
  cleanup();
  queryClient?.clear();
  vi.restoreAllMocks();
});

describe("a stored choice label with auto-annotation on", () => {
  it("sends its stored settings when only the name changes", async () => {
    const { put, post } = openLabelForm(STORED_AUTO_CHOICE);

    await typeName(NEW_NAME);
    await pressSave();

    await expectUpdated(
      put,
      STORED_AUTO_CHOICE,
      NEW_NAME,
      STORED_AUTO_CHOICE.settings,
    );
    expect(post).not.toHaveBeenCalled();
  });

  it("keeps its auto-annotation when Multi Choice is turned on", async () => {
    const { put, post } = openLabelForm(STORED_AUTO_CHOICE);

    await act(async () => {
      fireEvent.click(multiChoiceBox());
    });
    await pressSave();

    await expectUpdated(put, STORED_AUTO_CHOICE, STORED_AUTO_CHOICE.name, {
      ...STORED_AUTO_CHOICE.settings,
      multi_choice: true,
    });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a stored choice label without auto-annotation settings", () => {
  it("sends the defaults when only the name changes", async () => {
    const { put, post } = openLabelForm(STORED_PLAIN_CHOICE);

    await typeName(NEW_NAME);
    await pressSave();

    await expectUpdated(put, STORED_PLAIN_CHOICE, NEW_NAME, {
      options: [{ label: "Happy" }, { label: "Sad" }],
      multi_choice: false,
      rule_prompt: "",
      auto_annotate: false,
      strategy: null,
    });
    expect(post).not.toHaveBeenCalled();
  });
});

describe("a new choice label", () => {
  it("is saved with auto-annotation off", async () => {
    const { put, post } = openLabelForm(null);

    await chooseType("Categorical");
    await addChoice("Calm");
    await addChoice("Tense");
    await typeName("Tone");
    await pressSave();

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith(endpoints.project.createLabel(), {
      name: "Tone",
      type: "categorical",
      description: "",
      project: PROJECT_ID,
      settings: {
        options: [{ label: "Calm" }, { label: "Tense" }],
        multi_choice: false,
        rule_prompt: "",
        auto_annotate: false,
        strategy: null,
      },
    });
    expect(put).not.toHaveBeenCalled();
  });
});
