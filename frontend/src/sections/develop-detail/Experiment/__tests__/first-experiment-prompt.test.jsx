import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import { endpoints } from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import RunTargetPicker from "../RunTargetPicker";
import FirstPromptComposer from "../FirstPromptComposer";
import ConfigureStepLLM from "../StepperComponentExperiment/ConfigureStepLLM";
import { ExperimentFormContext } from "../ExperimentFormContext";

// JRN-EXP-NO-PROMPT-DEADEND (first-user journey, step 3): a brand-new user
// opens New experiment on their first dataset. The Prompt card said "No
// saved prompts" and sent them to Prompts in a new tab. Now the prompt is
// written in the wizard and saved as a real prompt (a saved v1), so the run
// card is the same one a saved prompt makes. Seams: the prompt catalogue
// read, the create request and the model menu; the picker, the composer and
// the Configuration step are the real components.
const { reads, post, MODEL } = vi.hoisted(() => ({
  reads: { prompts: null },
  post: vi.fn(),
  MODEL: {
    model_name: "gpt-4o-mini",
    providers: "openai",
    logoUrl: "",
    isAvailable: true,
    id: "model-1",
    type: "chat",
  },
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(() =>
        Promise.reject(
          new Error("Unexpected GET in the first-experiment test"),
        ),
      ),
      post: (...args) => post(...args),
    },
  };
});
vi.mock("src/api/develop/prompt", async (importOriginal) => ({
  ...(await importOriginal()),
  usePromptExecutions: () => reads.prompts,
}));
vi.mock("src/api/experiment/use-get-agents", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetListOfAgents: () => ({
    isPending: true,
    isError: false,
    data: undefined,
    refetch: () => {},
  }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-model-dropdown/CustomModelDropdown", () => ({
  // eslint-disable-next-line react/prop-types
  default: function ModelMenuStub({ value, onChange }) {
    return (
      <button
        type="button"
        onClick={() => onChange({ target: { value: MODEL } })}
      >
        {value || "Choose model"}
      </button>
    );
  },
}));
vi.mock("../StepperComponentExperiment/AgentPromptRenderer", () => ({
  // eslint-disable-next-line react/prop-types
  default: function PromptRunCardStub({ prompt }) {
    return <div data-testid="prompt-run-card">{prompt?.name}</div>;
  },
}));

const columns = [
  { id: "c1", name: "question" },
  { id: "c2", name: "customer tier" },
];
const formContext = {
  datasetId: null,
  datasetName: "support-faq",
  isEditing: false,
  nextOrdinal: null,
  columns: [],
  mappingColumns: columns,
  inputColumn: "question",
  jsonSchemas: {},
  rows: [],
  sampleRow: null,
  sampleRowLabel: "",
  setSampleRowId: () => {},
  exampleCount: 3,
  versions: [],
  splits: [],
};
const settledRead = (pages) => ({
  isPending: false,
  isError: false,
  isFetching: false,
  data: { pages },
  refetch: vi.fn(),
});
const created = {
  data: {
    status: true,
    result: {
      id: "p-new",
      root_template: "p-new",
      name: "support-faq prompt",
      created_version: "v1",
    },
  },
};

function FirstRunProbe({ control }) {
  const cards = useWatch({ control, name: "promptConfig" }) ?? [];
  return (
    <output aria-label="runs">
      {JSON.stringify(
        cards.map((c) => [
          c.targetType,
          c.promptId,
          c.name,
          c.model?.[0]?.value,
        ]),
      )}
    </output>
  );
}
FirstRunProbe.propTypes = { control: PropTypes.object.isRequired };

function ConfigureHarness() {
  const { control, setValue, getValues, watch, unregister } = useForm({
    defaultValues: { promptConfig: [] },
  });
  return (
    <>
      <ConfigureStepLLM
        control={control}
        setValue={setValue}
        getValues={getValues}
        watch={watch}
        unregister={unregister}
      />
      <FirstRunProbe control={control} />
    </>
  );
}

const clients = [];
function renderWithClient(ui) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <ExperimentFormContext.Provider value={formContext}>
        {ui}
      </ExperimentFormContext.Provider>
    </QueryClientProvider>,
  );
}

let anchor;
beforeEach(() => {
  anchor = document.createElement("button");
  anchor.textContent = "Prompt";
  document.body.appendChild(anchor);
  reads.prompts = settledRead([{ results: [] }]);
  post.mockReset();
});
afterEach(() => {
  cleanup();
  anchor.remove();
  clients.splice(0).forEach((client) => client.clear());
  setLaunchOverrideForTest(null);
  vi.clearAllMocks();
});

describe("first experiment without a saved prompt (JRN-EXP-NO-PROMPT-DEADEND)", () => {
  it("FED-1: no saved prompts offers Write a prompt, which hands off to the wizard", () => {
    const onWrite = vi.fn();
    const onClose = vi.fn();
    const onPick = vi.fn();
    renderWithClient(
      <RunTargetPicker
        anchorEl={anchor}
        type="prompt"
        onPick={onPick}
        onClose={onClose}
        onWrite={onWrite}
      />,
    );

    expect(screen.getByText("No saved prompts")).toBeInTheDocument();
    expect(
      screen.getByText(/write one here without leaving this experiment/i),
    ).toBeInTheDocument();
    // Prompts stays one click away, in a new tab, for those who want it.
    const open = screen.getByRole("link", { name: /open prompts/i });
    expect(open.getAttribute("href")).toMatch(/^\/dashboard\/workbench/);
    expect(open).toHaveAttribute("target", "_blank");

    fireEvent.click(screen.getByRole("button", { name: "Write a prompt" }));
    expect(onWrite).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onPick).not.toHaveBeenCalled();
  });

  it("FED-1b: saved prompts are listed as before, and a picker without a write handler is unchanged", () => {
    const item = { id: "p1", name: "Support triage", model: "gpt-4o-mini" };
    reads.prompts = settledRead([{ results: [item] }]);
    const onPick = vi.fn();
    const view = renderWithClient(
      <RunTargetPicker
        anchorEl={anchor}
        type="prompt"
        onPick={onPick}
        onClose={vi.fn()}
        onWrite={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "Write a prompt" })).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: /support triage/i }));
    expect(onPick).toHaveBeenCalledWith(item);
    view.unmount();

    reads.prompts = settledRead([{ results: [] }]);
    renderWithClient(
      <RunTargetPicker
        anchorEl={anchor}
        type="prompt"
        onPick={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "Write a prompt" })).toBeNull();
    expect(
      screen.getByRole("link", { name: /create a prompt/i }),
    ).toHaveAttribute("target", "_blank");
  });

  it("FED-2: writes a prompt with the dataset's columns as variables and saves it as a real prompt", async () => {
    post.mockResolvedValueOnce(created);
    const onSaved = vi.fn();
    const onCancel = vi.fn();
    renderWithClient(
      <FirstPromptComposer onSaved={onSaved} onCancel={onCancel} />,
    );

    expect(screen.getByLabelText("Prompt name")).toHaveValue(
      "support-faq prompt",
    );
    const message = screen.getByLabelText("Message");
    expect(message).toHaveValue("{{question}}");
    fireEvent.change(message, { target: { value: "Answer briefly:" } });
    fireEvent.click(screen.getByRole("button", { name: "{{customer tier}}" }));
    expect(message).toHaveValue("Answer briefly: {{customer tier}}");

    // Nothing is sent before a model is picked; the card says so.
    fireEvent.click(screen.getByRole("button", { name: "Save and add run" }));
    expect(screen.getByTestId("first-prompt-issues")).toHaveTextContent(
      "Pick a model",
    );
    expect(post).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Choose model" }));
    fireEvent.click(screen.getByRole("button", { name: "Save and add run" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));

    expect(post).toHaveBeenCalledTimes(1);
    const [url, body] = post.mock.calls[0];
    expect(url).toBe(endpoints.develop.runPrompt.createPromptDraft);
    expect(body).toMatchObject({
      name: "support-faq prompt",
      is_draft: false,
      prompt_config: [
        {
          messages: [
            {
              role: "user",
              content: [
                { type: "text", text: "Answer briefly: {{customer tier}}" },
              ],
            },
          ],
          // A chat prompt: the Prompt picker lists it from now on.
          configuration: {
            model: "gpt-4o-mini",
            model_detail: { type: "chat" },
          },
        },
      ],
    });
    expect(onSaved).toHaveBeenCalledWith({
      id: "p-new",
      name: "support-faq prompt",
      model: "gpt-4o-mini",
      model_detail: { logo_url: "", providers: "openai" },
    });
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("FED-2b: a refused save keeps what was written and says why", async () => {
    post.mockRejectedValueOnce({
      status: false,
      result: "Failed to create draft",
      statusCode: 400,
    });
    const onSaved = vi.fn();
    renderWithClient(
      <FirstPromptComposer onSaved={onSaved} onCancel={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Choose model" }));
    fireEvent.click(screen.getByRole("button", { name: "Save and add run" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to create draft",
    );
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Message")).toHaveValue("{{question}}");
  });

  it("FED-2c: a variable that is not a column blocks saving; Cancel leaves", () => {
    const onCancel = vi.fn();
    renderWithClient(
      <FirstPromptComposer onSaved={vi.fn()} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Choose model" }));
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Answer {{questoin}}" },
    });
    expect(
      screen.getByText(
        "{{questoin}} isn't a column in this dataset. Pick one from the list below.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save and add run" }));
    expect(post).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("FED-3: from the Prompt card, the first run is written and added without leaving the wizard", async () => {
    // The wizard as the launch build shows it (New experiment enhancements on).
    setLaunchOverrideForTest({ launch: false });
    post.mockResolvedValueOnce(created);
    renderWithClient(<ConfigureHarness />);

    fireEvent.click(screen.getByTestId("source-card-prompt"));
    fireEvent.click(
      await screen.findByRole("button", { name: "Write a prompt" }),
    );
    expect(
      await screen.findByTestId("first-prompt-composer"),
    ).toBeInTheDocument();
    // The picker has closed, so the drawer is in reach again.
    fireEvent.click(
      await screen.findByRole("button", { name: "Choose model" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save and add run" }));

    await waitFor(() =>
      expect(screen.getByLabelText("runs")).toHaveTextContent(
        JSON.stringify([
          ["prompt", "p-new", "support-faq prompt", "gpt-4o-mini"],
        ]),
      ),
    );
    expect(screen.queryByTestId("first-prompt-composer")).toBeNull();
    expect(screen.getByTestId("prompt-run-card")).toHaveTextContent(
      "support-faq prompt",
    );
  });
});
