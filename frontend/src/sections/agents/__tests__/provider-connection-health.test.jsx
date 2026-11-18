import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";

// Keep the real `endpoints` so the URLs are the ones the page really reads.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: vi.fn(), post: vi.fn() } };
});

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ orgLimit: {} }),
}));

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useParams: () => ({ agentDefinitionId: "agent-1" }) };
});

vi.mock("src/api/knowledge-base/files", () => ({
  useKnowledgeBaseList: () => ({ data: [] }),
}));

vi.mock("src/utils/Mixpanel", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, trackEvent: vi.fn(), handleOnDocsClicked: vi.fn() };
});

const { default: axios, endpoints } = await import("src/utils/axios");
const { useAgentDetailsStore } = await import("../store/agentDetailsStore");
const { default: AgentConfigurationView } = await import(
  "../AgentConfiguration/AgentConfigurationView"
);
const { describeConfigurationHealth } = await import(
  "../AgentConfiguration/providerConnectionHealth"
);

const AGENT_URL = endpoints.agentDefinitions.detail("agent-1");
const PROVIDER_URL = endpoints.agentDefinitions.observabilityProvider("prov-1");

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

// A hosted Retell agent with an assistant ID, no key, and call import on.
const RETELL_NO_KEY = {
  id: "v1",
  agent_kind: "hosted",
  configuration_snapshot: {
    agent_name: "Support agent",
    agent_type: "voice",
    provider: "retell",
    authentication_method: "api_key",
    languages: ["en"],
    assistant_id: "agent_abc",
    observability_enabled: true,
    description: "Answers support questions",
    inbound: true,
    contact_number: "",
    commit_message: "",
  },
};

const NEXT_RETRY = "2026-11-18T15:30:00+00:00";
const MISSING_KEY = {
  status: "misconfigured",
  reason_code: "missing_key",
  first_failed_at: "2026-11-18T14:00:00+00:00",
  last_failed_at: "2026-11-18T14:30:00+00:00",
  failure_count: 2,
  next_retry_at: NEXT_RETRY,
};

function serve({ health, providerId = "prov-1" }) {
  axios.get.mockImplementation(async (url) => {
    if (url === AGENT_URL) {
      return { data: { id: "agent-1", observability_provider: providerId } };
    }
    if (url === PROVIDER_URL) {
      return { data: { id: "prov-1", configuration_health: health } };
    }
    return { data: {} };
  });
}

const mount = () => {
  useAgentDetailsStore.setState({
    agentDetails: RETELL_NO_KEY,
    selectedVersion: "v1",
  });
  return render(
    <Providers>
      <AgentConfigurationView />
    </Providers>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("provider connection health on the agent page", () => {
  it("warns that calls can't be imported, with the reason and next try", async () => {
    serve({ health: MISSING_KEY });
    mount();

    const alert = await screen.findByTestId("provider-health");
    expect(alert).toHaveTextContent("Can't import calls: API key missing.");
    expect(alert).toHaveTextContent("Next try at");
    expect(axios.get).toHaveBeenCalledWith(PROVIDER_URL);
  });

  it("shows nothing once the connection is healthy again", async () => {
    serve({ health: null });
    mount();

    await screen.findByText("Enable Observability");
    await waitFor(() => expect(axios.get).toHaveBeenCalledWith(PROVIDER_URL));
    expect(screen.queryByTestId("provider-health")).not.toBeInTheDocument();
  });

  it("does not ask for provider health when no connection is linked", async () => {
    serve({ health: MISSING_KEY, providerId: null });
    mount();

    await screen.findByText("Enable Observability");
    await waitFor(() => expect(axios.get).toHaveBeenCalledWith(AGENT_URL));
    expect(axios.get).not.toHaveBeenCalledWith(PROVIDER_URL);
    expect(screen.queryByTestId("provider-health")).not.toBeInTheDocument();
  });
});

describe("describeConfigurationHealth", () => {
  it("is null for a healthy connection", () => {
    expect(describeConfigurationHealth(null)).toBeNull();
    expect(describeConfigurationHealth(undefined)).toBeNull();
  });

  it.each([
    ["missing_key", "API key missing"],
    ["missing_assistant_id", "assistant ID missing"],
    ["missing_agent", "no agent is linked to this connection"],
    ["deleted_agent", "the linked agent was deleted"],
    ["credential_unreadable", "the saved API key can't be read"],
    ["something_new", "the connection is not set up"],
  ])("explains %s", (reason_code, text) => {
    expect(
      describeConfigurationHealth({ ...MISSING_KEY, reason_code }),
    ).toContain(text);
  });

  it("leaves out the next try when it is missing or unreadable", () => {
    for (const next_retry_at of [undefined, null, "not a date"]) {
      expect(
        describeConfigurationHealth({ ...MISSING_KEY, next_retry_at }),
      ).toBe("Can't import calls: API key missing.");
    }
  });
});
