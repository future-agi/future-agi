import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";

// On Future AGI Cloud the backend does not contact a customer's LiveKit URL to
// "test" credentials (T6 #7b): it answers 200 with skipped: true and says the
// credentials were not verified. The forms must show that as not verified,
// never as a successful connection, and must still let the user save.

const axiosMocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = /** @type {typeof import("src/utils/axios")} */ (
    await importOriginal()
  );
  return {
    ...actual,
    default: { get: axiosMocks.get, post: axiosMocks.post },
  };
});

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ orgLimit: {}, role: "Owner" }),
}));

vi.mock("react-router", async (importOriginal) => {
  const actual = /** @type {typeof import("react-router")} */ (
    await importOriginal()
  );
  return {
    ...actual,
    useParams: () => ({ agentDefinitionId: "agent-1" }),
    useNavigate: () => vi.fn(),
  };
});

vi.mock("src/api/knowledge-base/files", () => ({
  useKnowledgeBaseList: () => ({ data: [] }),
}));

vi.mock("notistack", async (importOriginal) => {
  const actual = /** @type {typeof import("notistack")} */ (
    await importOriginal()
  );
  return {
    ...actual,
    enqueueSnackbar: vi.fn(),
    useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
  };
});

vi.mock("src/utils/Mixpanel", async (importOriginal) => {
  const actual = /** @type {typeof import("src/utils/Mixpanel")} */ (
    await importOriginal()
  );
  return { ...actual, trackEvent: vi.fn(), handleOnDocsClicked: vi.fn() };
});

// The create wizard starts on a LiveKit voice agent whose fields are filled.
vi.mock("../helper", async (importOriginal) => {
  const actual = /** @type {typeof import("../helper")} */ (
    await importOriginal()
  );
  return {
    ...actual,
    defaultAgentDefinitionValues: {
      ...actual.defaultAgentDefinitionValues,
      agentType: "voice",
      agentName: "Returns agent",
      languages: ["en"],
      provider: "livekit",
      livekitUrl: "wss://demo.livekit.cloud",
      livekitApiKey: "APIcustomer-key",
      livekitApiSecret: "customer-plaintext-secret-32chars",
      livekitAgentName: "returns-agent",
    },
  };
});

const { endpoints } = await import("src/utils/axios");
const { validateLiveKitCredentials } = await import("../constants");
const { useAgentDetailsStore } = await import("../store/agentDetailsStore");
const { useCreateNewAgentStore } = await import("../store/createNewAgentStore");
const { default: AgentConfigurationView } = await import(
  "../AgentConfiguration/AgentConfigurationView"
);
const { default: CreateNewAgentDefinitionView } = await import(
  "../CreateNewAgentDefinitionView"
);

// The wizard's Next button also holds an icon; match its label only.
const NEXT = /^Next\b/;
const VALIDATE_URL = endpoints.runTests.validateLiveKitCredentials;
const CREATE_VERSION_URL = endpoints.agentDefinitions.createVersion("agent-1");
const NOT_VERIFIED =
  "LiveKit credentials not verified on Future AGI Cloud: Future AGI does not " +
  "contact customer LiveKit URLs from its servers; connection problems will " +
  "show when a run connects.";
const HOSTED_ANSWER = {
  status: true,
  result: {
    valid: false,
    verified: false,
    skipped: true,
    message: NOT_VERIFIED,
  },
};

const testTheme = createTheme({
  palette: palette("light"),
  spacing: (factor) => `${0.25 * factor}rem`,
});

function Providers({ children }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider theme={testTheme}>{children}</ThemeProvider>
    </QueryClientProvider>
  );
}
Providers.propTypes = { children: PropTypes.node };

const answerValidation = (body) =>
  axiosMocks.post.mockImplementation(async (url) => {
    if (url === VALIDATE_URL) return { data: body };
    if (url === CREATE_VERSION_URL) {
      return { data: { message: "saved", version: { id: "v2" } } };
    }
    return { data: {} };
  });

const validateCalls = () =>
  axiosMocks.post.mock.calls.filter(([url]) => url === VALIDATE_URL);

beforeEach(() => {
  vi.clearAllMocks();
  axiosMocks.get.mockResolvedValue({ data: {} });
  answerValidation(HOSTED_ANSWER);
});

describe("validateLiveKitCredentials", () => {
  const args = {
    livekitUrl: "wss://demo.livekit.cloud",
    livekitApiKey: "APIcustomer-key",
    livekitApiSecret: "customer-plaintext-secret-32chars",
    livekitAgentName: "returns-agent",
  };

  it("reports a check the server skipped as skipped, not valid, with its message", async () => {
    const result = await validateLiveKitCredentials(args);

    expect(result).toEqual({
      valid: false,
      skipped: true,
      message: NOT_VERIFIED,
    });
  });

  it("still reports a verified connection as valid", async () => {
    answerValidation({ status: true, result: { valid: true } });

    expect(await validateLiveKitCredentials(args)).toEqual({ valid: true });
  });

  it("still reports a rejected key as invalid with the server's error", async () => {
    answerValidation({
      status: true,
      result: { valid: false, error: "401 unauthorized" },
    });

    expect(await validateLiveKitCredentials(args)).toEqual({
      valid: false,
      error: "401 unauthorized",
    });
  });
});

describe("create wizard on Future AGI Cloud", () => {
  it("shows not verified, never success, and lets the user go on", async () => {
    useCreateNewAgentStore.setState({ currentStep: 1 });
    render(
      <Providers>
        <CreateNewAgentDefinitionView />
      </Providers>,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Test Connection" }),
    );

    expect(await screen.findByText(NOT_VERIFIED)).toBeInTheDocument();
    expect(validateCalls().length).toBeGreaterThan(0);
    expect(screen.queryByText("Connection successful")).toBeNull();
    expect(screen.queryByText("Validation failed")).toBeNull();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: NEXT })).toBeEnabled(),
    );
  });

  it("still blocks the step when the server rejects the credentials", async () => {
    answerValidation({
      status: true,
      result: { valid: false, error: "401 unauthorized" },
    });
    useCreateNewAgentStore.setState({ currentStep: 1 });
    render(
      <Providers>
        <CreateNewAgentDefinitionView />
      </Providers>,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Test Connection" }),
    );

    expect(await screen.findByText("401 unauthorized")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: NEXT })).toBeDisabled();
  });
});

describe("agent page on Future AGI Cloud", () => {
  const seedLiveKitAgent = () =>
    useAgentDetailsStore.setState({
      agentDetails: {
        id: "v1",
        agent_kind: "hosted",
        configuration_snapshot: {
          agent_name: "Returns agent",
          agent_type: "voice",
          provider: "livekit",
          languages: ["en"],
          description: "Handles returns",
          inbound: true,
          contact_number: "",
          commit_message: "",
          observability_enabled: false,
          livekit_url: "wss://demo.livekit.cloud",
          livekit_api_key: "APIcustomer-key",
          livekit_api_secret: "customer-plaintext-secret-32chars",
          livekit_agent_name: "returns-agent",
        },
      },
      selectedVersion: "v1",
    });

  it("labels a skipped check not verified, never connected, and still saves", async () => {
    seedLiveKitAgent();
    render(
      <Providers>
        <AgentConfigurationView />
      </Providers>,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Test Connection" }),
    );

    expect(await screen.findByText(NOT_VERIFIED)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Not verified" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connected" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Failed" })).toBeNull();

    fireEvent.change(
      screen.getByLabelText(/Commit Message|What's changing in this version/),
      { target: { value: "Rotate the LiveKit key" } },
    );
    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(
        axiosMocks.post.mock.calls.find(([url]) => url === CREATE_VERSION_URL),
      ).toBeDefined(),
    );
    const [, body] = axiosMocks.post.mock.calls.find(
      ([url]) => url === CREATE_VERSION_URL,
    );
    expect(body.livekit_url).toBe("wss://demo.livekit.cloud");
    expect(body.livekit_api_key).toBe("APIcustomer-key");
  });
});
