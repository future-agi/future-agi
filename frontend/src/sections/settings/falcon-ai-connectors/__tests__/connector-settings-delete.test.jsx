import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
  act,
} from "src/utils/test-utils";

import ConnectorSettingsPage from "../ConnectorSettingsPage";

// Mocked at the transport layer so the real `useConnector` query runs: the
// hook closes over its module's own `getConnector`, so replacing that export
// would leave the hook calling the original.
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
    },
  },
}));

const LIST_ROW = {
  id: "conn-1",
  name: "DeepWiki",
  server_url: "https://mcp.deepwiki.com/mcp",
  transport: "streamable_http",
  auth_type: "none",
  is_verified: true,
  tool_count: 2,
};

const DETAIL = {
  ...LIST_ROW,
  discovered_tools: [
    { name: "ask_question", description: "Ask about a repo." },
    { name: "read_wiki_contents", description: "View documentation." },
  ],
  enabled_tool_names: ["ask_question", "read_wiki_contents"],
};

const CONNECTOR_URL = "/falcon-ai/mcp-connectors/conn-1/";

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

// Opens the connector and waits for the detail route's tools to render.
const openConnector = async () => {
  mocks.get.mockImplementation((url) => {
    if (url === "/falcon-ai/mcp-connectors/") {
      return Promise.resolve({ data: { results: [LIST_ROW] } });
    }
    if (url === CONNECTOR_URL) {
      return Promise.resolve({ data: { result: DETAIL } });
    }
    return Promise.resolve({ data: {} });
  });

  renderPage();
  fireEvent.click(await screen.findByText("DeepWiki"));
  await screen.findByText("ask_question");
};

// The Delete button in the connector's action row.
const clickDelete = () =>
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));

describe("Connector settings — delete", () => {
  beforeEach(() => vi.clearAllMocks());

  it("asks before deleting, and Cancel keeps the connector", async () => {
    mocks.delete.mockResolvedValue({ data: {} });
    await openConnector();

    clickDelete();

    // A connector holds a secret or OAuth grant that cannot be recovered, so
    // one click must not delete it.
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Delete connector?");
    expect(dialog).toHaveTextContent(
      "Falcon AI will lose access to DeepWiki and its 2 tools.",
    );
    expect(mocks.delete).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(mocks.delete).not.toHaveBeenCalled();
    expect(screen.getByText("ask_question")).toBeInTheDocument();
  });

  it("deletes once confirmed, blocks a second click, and says it is done", async () => {
    let release;
    mocks.delete.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () => resolve({ data: {} });
        }),
    );
    await openConnector();

    clickDelete();

    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", {
      name: "Delete connector",
    });
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(mocks.delete).toHaveBeenCalledWith(CONNECTOR_URL),
    );
    await waitFor(() => expect(confirm).toBeDisabled());

    await act(async () => {
      release();
    });

    await waitFor(() =>
      expect(screen.queryByText("DeepWiki")).not.toBeInTheDocument(),
    );
    expect(mocks.delete).toHaveBeenCalledTimes(1);
    expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
      "Connector deleted",
      expect.objectContaining({ variant: "success" }),
    );
  });

  it.each([
    [
      "the server refuses",
      // Shape the axios interceptor rejects with: the body plus statusCode.
      {
        status: false,
        result: "Connector not found",
        code: "not_found",
        statusCode: 404,
      },
      "Connector not found",
    ],
    [
      "the network drops",
      { message: "Network Error", transportCode: "ERR_NETWORK" },
      "Connector could not be deleted. Try again.",
    ],
  ])(
    "says the delete failed when %s, and keeps the connector",
    async (_label, rejection, message) => {
      mocks.delete.mockRejectedValue(rejection);
      await openConnector();

      clickDelete();

      const dialog = await screen.findByRole("dialog");
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Delete connector" }),
      );

      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(await screen.findByRole("alert")).toHaveTextContent(message);
      expect(screen.getByText("ask_question")).toBeInTheDocument();
      expect(screen.getAllByText("DeepWiki").length).toBeGreaterThan(0);
      expect(mocks.enqueueSnackbar).not.toHaveBeenCalledWith(
        "Connector deleted",
        expect.anything(),
      );
    },
  );
});
