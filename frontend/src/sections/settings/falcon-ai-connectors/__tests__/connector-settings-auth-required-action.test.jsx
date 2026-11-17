import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";

import ConnectorSettingsPage from "../ConnectorSettingsPage";
import {
  CONNECTOR_AUTH_REQUIRED,
  CONNECTOR_AUTH_REQUIRED_MESSAGE,
} from "src/sections/falcon-ai/components/connectorErrors";

// Mocked at the transport layer so the real `useConnector` query runs.
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => mocks.enqueueSnackbar(...args),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: mocks.get,
    post: mocks.post,
    patch: mocks.patch,
    delete: mocks.delete,
  },
  endpoints: {
    falconAI: {
      connectors: "/falcon-ai/mcp-connectors/",
      connector: (id) => `/falcon-ai/mcp-connectors/${id}/`,
      connectorTools: (id) => `/falcon-ai/mcp-connectors/${id}/tools/`,
      connectorTest: (id) => `/falcon-ai/mcp-connectors/${id}/test/`,
    },
  },
}));

// The connector API refuses Re-authenticate and Test Connection when the
// credential saved for the connector can't be read here. The shared client
// rejects with the response body spread flat: the code is both its code and
// its message.
const REFUSAL = {
  status: false,
  message: CONNECTOR_AUTH_REQUIRED,
  code: CONNECTOR_AUTH_REQUIRED,
  statusCode: 409,
};

// An OAuth connector (the kind that has Re-authenticate here) with no error
// kept on it, so only the refused action can say anything.
const ROW = {
  id: "conn-1",
  name: "Linear",
  server_url: "https://mcp.linear.app/mcp",
  transport: "streamable_http",
  auth_type: "oauth",
  is_verified: true,
  tool_count: 1,
};

const DETAIL = {
  ...ROW,
  discovered_tools: [{ name: "list_issues", description: "List issues." }],
  enabled_tool_names: ["list_issues"],
  last_error: "",
};

const renderPage = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ConnectorSettingsPage />
    </QueryClientProvider>,
  );

const openConnector = async () => {
  mocks.get.mockImplementation((url) => {
    if (url === "/falcon-ai/mcp-connectors/") {
      return Promise.resolve({ data: { results: [ROW] } });
    }
    if (url === "/falcon-ai/mcp-connectors/conn-1/") {
      return Promise.resolve({ data: { result: DETAIL } });
    }
    return Promise.resolve({ data: {} });
  });

  renderPage();
  fireEvent.click(await screen.findByText("Linear"));
  await screen.findByText("list_issues");
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.post.mockRejectedValue(REFUSAL);
});

describe("Connector settings — an action refused because the saved credential can't be read here", () => {
  it.each(["Re-authenticate", "Test Connection"])(
    "%s says so in words, never with the code",
    async (action) => {
      await openConnector();
      expect(screen.queryByText(CONNECTOR_AUTH_REQUIRED_MESSAGE)).toBeNull();

      fireEvent.click(screen.getByRole("button", { name: action }));

      await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
      // The notice under the connector used to show the code itself.
      const notice = await screen.findByText(CONNECTOR_AUTH_REQUIRED_MESSAGE);
      expect(notice.closest('[role="alert"]')).not.toBeNull();
      expect(screen.queryByText(CONNECTOR_AUTH_REQUIRED)).toBeNull();
      expect(document.body).not.toHaveTextContent(CONNECTOR_AUTH_REQUIRED);
    },
  );
});
