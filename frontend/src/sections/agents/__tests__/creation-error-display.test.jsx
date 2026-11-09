import React from "react";
import PropTypes from "prop-types";
import { FormProvider, useForm } from "react-hook-form";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { handleError } from "src/utils/queryErrorHandler";
import CreateNewAgentDefinitionView from "../CreateNewAgentDefinitionView";
import CreateScenariosForm from "src/sections/projects/SessionsView/ReplaySessions/CreateScenariosForm";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  snackbar: vi.fn(),
  navigate: vi.fn(),
  reset: vi.fn(),
  setError: vi.fn(),
}));

// Observe the otherwise private direct-form error state, preserving real React
// state updates. The request handler, form submission and mutation stay real.
vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useState: (initial) => {
      const [value, setValue] = actual.useState(initial);
      return [
        value,
        initial === ""
          ? (next) => {
              mocks.setError(next);
              setValue(next);
            }
          : setValue,
      ];
    },
  };
});
vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    agentDefinitions: {
      create: "/simulate/agent-definitions/create/",
      createVersion: (id) =>
        `/simulate/agent-definitions/${id}/versions/create/`,
    },
  },
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: mocks.snackbar,
  useSnackbar: () => ({ enqueueSnackbar: mocks.snackbar }),
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => mocks.navigate,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("../helper", async () => {
  const { z } = await import("zod");
  return {
    createAgentDefinitionSchema: () => z.any(),
    stepFields: [[], [], []],
    defaultAgentDefinitionValues: {
      agentType: "text",
      agentName: "Copy test",
      languages: ["en"],
      description: "Test prompt",
      commitMessage: "Initial version",
    },
  };
});
vi.mock("../store/createNewAgentStore", () => ({
  useCreateNewAgentStore: () => ({
    currentStep: 2,
    reset: mocks.reset,
    nextStep: vi.fn(),
    prevStep: vi.fn(),
    setStepValidated: vi.fn(),
  }),
}));
vi.mock("../CreateNewAgent/StepsTracker", () => ({ default: () => null }));
vi.mock(
  "../CreateNewAgent/AgentConfigurationStep/AgentConfigurationStep",
  () => ({ default: () => null }),
);
vi.mock(
  "../CreateNewAgent/AgentBasicInfoStep/AgentBasicInfoStepRightSection",
  () => ({ default: () => null }),
);
vi.mock("../CreateNewAgent/AgentBasicInfoStep/AgentBasicInfoStep", () => ({
  default: () => null,
}));
vi.mock(
  "../CreateNewAgent/AgentConfigurationStep/AgentConfigurationStepRightSection",
  () => ({ default: () => null }),
);
vi.mock(
  "../CreateNewAgent/AgentBehaviourStep/AgentBehaviourStepRightSection",
  () => ({ default: () => null }),
);
vi.mock("../CreateNewAgent/AgentBehaviourStep/AgentBehaviourStep", () => ({
  default: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/svg-color/svg-color", () => ({ default: () => null }));
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: () => null,
}));
vi.mock("src/sections/scenarios/PersonaSection", () => ({
  default: () => null,
}));
vi.mock("src/components/ExpandableTextField/ExpandableTextFiels", () => ({
  default: ({ children, otherActions }) => (
    <div>
      {children}
      {otherActions}
    </div>
  ),
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children, title }) => <span aria-label={title}>{children}</span>,
}));
vi.mock(
  "src/sections/projects/SessionsView/ReplaySessions/useReplayConfiguration",
  () => ({
    useReplayConfiguration: () => ({
      textContent: { agentDefinitionDescription: "Replay prompt" },
    }),
  }),
);
vi.mock("src/sections/projects/SessionsView/ReplaySessions/store", () => ({
  useReplaySessionsStoreShallow: (selector) =>
    selector({
      createdReplay: {
        id: "PRIVATE_REPLAY_ID",
        suggestions: { agentType: "text" },
      },
    }),
  useSessionsGridStoreShallow: (selector) =>
    selector({
      selectAll: false,
      toggledNodes: [],
      totalRowCount: 0,
    }),
}));

const GUIDANCE =
  "Agent definition creation could not be confirmed. Check the agent definitions before trying again.";
const MARKER = "PRIVATE_DRIVER_MARKER";
const INTERNAL_ID = "PRIVATE_RECORD_ID";
const poisonedBody = {
  code: "server_error",
  message: `${MARKER} ${INTERNAL_ID}`,
  result: `${MARKER} ${INTERNAL_ID}`,
  error: `${MARKER} ${INTERNAL_ID}`,
  detail: `${MARKER} ${INTERNAL_ID}`,
  details: { non_field_errors: [`${MARKER} ${INTERNAL_ID}`] },
  id: INTERNAL_ID,
};
const failures = [
  ["flattened", { ...poisonedBody, statusCode: 500 }],
  ["nested", { response: { status: 500, data: poisonedBody } }],
  [
    "misleading typed code",
    { ...poisonedBody, statusCode: 500, code: "permission_denied" },
  ],
];
const clients = [];

function ReplayForm({ existing = false }) {
  const methods = useForm({
    defaultValues: {
      hasAgentPrompt: true,
      agentDefExists: existing,
      agentType: "text",
      agentDefinition: {
        name: "Replay agent",
        prompt: "Replay prompt",
        version: "v1",
        ...(existing ? { id: "existing-agent" } : {}),
      },
    },
  });
  return (
    <FormProvider {...methods}>
      <CreateScenariosForm />
    </FormProvider>
  );
}

ReplayForm.propTypes = { existing: PropTypes.bool };

function renderReplay({ existing = false, retry = false } = {}) {
  const globalError = vi.fn(handleError);
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: globalError }),
    defaultOptions: { mutations: { retry, retryDelay: 0 } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <ReplayForm existing={existing} />
    </QueryClientProvider>,
  );
  fireEvent.click(
    within(screen.getByLabelText("Save Agent Definition")).getByRole("button"),
  );
  return { client, globalError };
}

function expectSafeGuidance() {
  expect(mocks.snackbar.mock.calls).toEqual([[GUIDANCE, { variant: "error" }]]);
  const displayed = JSON.stringify(mocks.snackbar.mock.calls);
  for (const forbidden of [
    MARKER,
    INTERNAL_ID,
    "PRIVATE_REPLAY_ID",
    "Something went wrong",
    "Please try again.",
  ]) {
    expect(displayed).not.toContain(forbidden);
  }
  expect(mocks.post).toHaveBeenCalledTimes(1);
  expect(mocks.navigate).not.toHaveBeenCalled();
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("CBSEC direct creation display", () => {
  it.each(failures)(
    "direct_create_failure_shows_check_before_retry_guidance: %s",
    async (_name, error) => {
      mocks.post.mockRejectedValue(error);
      render(<CreateNewAgentDefinitionView />);
      fireEvent.click(
        screen.getByRole("button", { name: "Create agent definition" }),
      );
      await waitFor(() => expect(mocks.snackbar).toHaveBeenCalled());
      expectSafeGuidance();
      expect(mocks.setError).toHaveBeenCalledWith(GUIDANCE);
      expect(JSON.stringify(mocks.setError.mock.calls)).not.toContain(MARKER);
      expect(JSON.stringify(mocks.setError.mock.calls)).not.toContain(
        INTERNAL_ID,
      );
    },
  );

  it("keeps successful direct creation and navigation", async () => {
    mocks.post.mockResolvedValue({ data: { agent: { id: "created-agent" } } });
    render(<CreateNewAgentDefinitionView />);
    fireEvent.click(
      screen.getByRole("button", { name: "Create agent definition" }),
    );
    await waitFor(() => expect(mocks.navigate).toHaveBeenCalled());
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.snackbar).toHaveBeenCalledWith("Agent created successfully!", {
      variant: "success",
    });
  });
});

describe("CBSEC replay creation display", () => {
  it.each(failures)(
    "replay_create_failure_shows_check_before_retry_guidance: %s",
    async (_name, error) => {
      mocks.post.mockRejectedValue(error);
      const { client, globalError } = renderReplay();
      await waitFor(() =>
        expect(client.getMutationCache().getAll()[0]?.state.status).toBe(
          "error",
        ),
      );
      expect(globalError).toHaveBeenCalledTimes(1);
      expectSafeGuidance();
      expect(mocks.post).toHaveBeenCalledWith(
        "/simulate/agent-definitions/create/",
        expect.objectContaining({ replay_session_id: "PRIVATE_REPLAY_ID" }),
      );
    },
  );

  it("never automatically repeats ambiguous creation even if the client enables retry", async () => {
    mocks.post.mockRejectedValue(failures[0][1]);
    const { client } = renderReplay({ retry: 1 });
    await waitFor(() =>
      expect(client.getMutationCache().getAll()[0]?.state.status).toBe("error"),
    );
    expectSafeGuidance();
  });

  it.each([
    [404, "not_found", "Replay session not found"],
    [400, "validation_error", "PROJECT CREATION LIMIT REACHED"],
  ])("preserves safe replay refusal %s", async (statusCode, code, result) => {
    mocks.post.mockRejectedValue({ statusCode, code, result });
    const { client } = renderReplay();
    await waitFor(() =>
      expect(client.getMutationCache().getAll()[0]?.state.status).toBe("error"),
    );
    expect(mocks.snackbar.mock.calls).toEqual([[result, { variant: "error" }]]);
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });

  it("keeps existing version-update error handling separate from creation", async () => {
    mocks.post.mockRejectedValue({
      statusCode: 400,
      result: "Version requires a commit message",
    });
    const { client, globalError } = renderReplay({ existing: true });
    await waitFor(() =>
      expect(client.getMutationCache().getAll()[0]?.state.status).toBe("error"),
    );
    expect(globalError).toHaveBeenCalledTimes(1);
    expect(mocks.snackbar.mock.calls).toEqual([
      ["Version requires a commit message", { variant: "error" }],
    ]);
    expect(mocks.post.mock.calls[0][0]).toBe(
      "/simulate/agent-definitions/existing-agent/versions/create/",
    );
  });

  it("keeps successful replay creation", async () => {
    mocks.post.mockResolvedValue({ data: { agent: { id: "created-agent" } } });
    renderReplay();
    await waitFor(() => expect(mocks.snackbar).toHaveBeenCalled());
    expect(mocks.snackbar.mock.calls).toEqual([
      ["Agent definition created successfully", { variant: "success" }],
    ]);
    expect(mocks.post).toHaveBeenCalledTimes(1);
  });
});
