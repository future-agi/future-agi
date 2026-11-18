/* eslint-disable react/prop-types, react/display-name -- Inline test doubles mirror third-party components. */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Controller } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";

const markers = {
  name: "SYNTHETIC_AGENT_NAME_a1b2",
  prompt: "SYNTHETIC_AGENT_PROMPT_c3d4",
  initialMessage: "SYNTHETIC_INITIAL_MESSAGE_e5f6",
  oldName: "SYNTHETIC_OLD_NAME_1234",
  oldPrompt: "SYNTHETIC_OLD_PROMPT_5678",
  oldMessage: "SYNTHETIC_OLD_MESSAGE_9abc",
};

const mocks = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic-project-token");
  return {
    mixpanel: {
      init: vi.fn(),
      track: vi.fn(),
      register: vi.fn(),
      get_config: vi.fn(),
      get_property: vi.fn(),
      set_config: vi.fn(),
      stop_session_recording: vi.fn(),
    },
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  };
});

vi.mock("mixpanel-browser", () => ({ default: mocks.mixpanel }));
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post, put: mocks.put },
  endpoints: {
    simulatorAgents: {
      list: "/simulate/simulator-agents/",
      create: "/simulate/simulator-agents/create/",
      edit: (id) => `/simulate/simulator-agents/${id}/edit/`,
    },
  },
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/components/simulator-agent/DeleteSimulatorAgentDialog", () => ({
  default: () => null,
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => null }));
vi.mock("src/utils/ag-grid-setup", () => ({}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: ({ columnDefs, serverSideDatasource }) => {
    // Exercise the real action column definition, not a substitute edit handler.
    const edit = columnDefs.find((column) => column.field === "actions")
      .cellRendererParams.onEdit;
    return (
      <button
        type="button"
        onClick={async () => {
          const rows = await new Promise((resolve) =>
            serverSideDatasource.getRows({
              request: { startRow: 0, endRow: 10 },
              success: ({ rowData }) => resolve(rowData),
              fail: () => resolve([]),
            }),
          );
          edit(rows[0]);
        }}
      >
        Edit agent row
      </button>
    );
  },
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock(
  "src/components/custom-model-dropdown/CustomModelDropdownControl",
  () => ({
    default: ({ control, fieldName }) => (
      <Controller
        name={fieldName}
        control={control}
        render={({ field }) => (
          <label>
            Language Model
            <input
              aria-label="Language Model"
              {...field}
              value={field.value ?? ""}
            />
          </label>
        )}
      />
    ),
  }),
);
vi.mock(
  "src/components/FromSearchSelectField/FormSearchSelectFieldState",
  () => ({
    default: React.forwardRef(({ label, options, value, onChange }, ref) => (
      <label>
        {label}
        <select
          ref={ref}
          aria-label={label}
          value={value ?? ""}
          onChange={onChange}
        >
          <option value="">Select</option>
          {options.map((option) => (
            <option
              key={option.value}
              value={option.value}
              disabled={option.disabled}
            >
              {option.label}
            </option>
          ))}
        </select>
      </label>
    )),
  }),
);

import SimulatorAgent from "../SimulatorAgent";
import { Events } from "src/utils/Mixpanel";

const oldAgent = {
  id: "sim-agent-17",
  name: markers.oldName,
  prompt: markers.oldPrompt,
  initialMessage: markers.oldMessage,
  model: "previous-model",
  agentType: "Voice",
  llmTemperature: 0.7,
  voiceProvider: "elevenlabs",
  voiceName: "marissa",
  interruptSensitivity: 5,
  conversationSpeed: 1,
  finishedSpeakingSensitivity: 5,
  maxCallDurationInMinutes: 5,
  initialMessageDelay: 1,
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <HelmetProvider>
      <QueryClientProvider client={client}>
        <SimulatorAgent />
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

function fillForm() {
  fireEvent.change(screen.getByRole("textbox", { name: "Agent Name" }), {
    target: { value: markers.name },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Prompt" }), {
    target: { value: markers.prompt },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Language Model" }), {
    target: { value: "synthetic-model" },
  });
  fireEvent.change(screen.getByRole("combobox", { name: "Agent type" }), {
    target: { value: "Voice" },
  });
  fireEvent.click(screen.getByRole("tab", { name: "Voice & Speech" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Voice Name" }), {
    target: { value: "burt" },
  });
  fireEvent.click(screen.getByRole("tab", { name: "Conversation" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Initial Message" }), {
    target: { value: markers.initialMessage },
  });
}

function submitForm(label) {
  // The default react-hook-form mode validates on submit, so isValid is false
  // until the first submission; submit the real form rather than a disabled button.
  const submit = screen.getByRole("button", { name: label });
  fireEvent.submit(submit.closest("form"));
}

const formData = {
  name: markers.name,
  prompt: markers.prompt,
  model: "synthetic-model",
  agentType: "Voice",
  llmTemperature: 0.7,
  voiceProvider: "elevenlabs",
  voiceName: "burt",
  interruptSensitivity: 5,
  conversationSpeed: 1,
  finishedSpeakingSensitivity: 5,
  maxCallDurationInMinutes: 5,
  initialMessageDelay: 1,
  initialMessage: markers.initialMessage,
};

const eventFields = {
  model: "synthetic-model",
  agentType: "Voice",
  llmTemperature: 0.7,
  voiceProvider: "elevenlabs",
  voiceName: "burt",
  interruptSensitivity: 5,
  conversationSpeed: 1,
  finishedSpeakingSensitivity: 5,
  maxCallDurationInMinutes: 5,
  initialMessageDelay: 1,
  prompt_length: markers.prompt.length,
  has_initial_message: true,
};

function expectTracked(event, properties) {
  const calls = mocks.mixpanel.track.mock.calls.filter(
    ([name]) => name === event,
  );
  expect(calls).toHaveLength(1);
  expect(calls[0][1]).toEqual(properties);
  const allCalls = JSON.stringify(mocks.mixpanel.track.mock.calls);
  Object.values(markers).forEach((value) =>
    expect(allCalls).not.toContain(value),
  );
}

describe("simulator agent page events", () => {
  beforeEach(() => {
    mocks.mixpanel.track.mockClear();
    mocks.get
      .mockReset()
      .mockResolvedValue({ data: { results: [oldAgent], count: 1 } });
    mocks.post.mockReset().mockResolvedValue({ data: {} });
    mocks.put.mockReset().mockResolvedValue({ data: {} });
  });

  it("sends the selected settings and prompt length when an agent is created", async () => {
    renderPage();
    await screen.findByRole("button", { name: "Edit agent row" });
    fireEvent.click(
      screen.getByRole("button", { name: "Add Simulator Agent" }),
    );
    await screen.findByRole("textbox", { name: "Agent Name" });
    fillForm();
    submitForm("Create Simulator");
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect(mocks.post).toHaveBeenCalledWith(
      "/simulate/simulator-agents/create/",
      formData,
    );
    expectTracked(Events.simulatorAgentCreatesimClicked, {
      form_fields: eventFields,
    });
  });

  it("sends the agent id and selected settings when an agent is edited", async () => {
    renderPage();
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit agent row" }),
    );
    await screen.findByRole("textbox", { name: "Agent Name" });
    fillForm();
    submitForm("Edit Simulator");
    await waitFor(() => expect(mocks.put).toHaveBeenCalledTimes(1));
    expect(mocks.put).toHaveBeenCalledWith(
      "/simulate/simulator-agents/sim-agent-17/edit/",
      formData,
    );
    expectTracked(Events.simulatorAgentEditsimSubmitted, {
      id: "sim-agent-17",
      form_fields: eventFields,
    });
  });
});
