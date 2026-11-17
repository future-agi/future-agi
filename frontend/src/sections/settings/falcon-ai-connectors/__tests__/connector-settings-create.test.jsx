import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent, act } from "src/utils/test-utils";

import ConnectorSettingsPage from "../ConnectorSettingsPage";

// W10-SETTINGS-TASKS-U06: after Create the page opens the new connector and
// says it was created, instead of ending on "Select a connector". The list
// reload after a save keeps the full-page loader, so nothing on the page can
// be clicked while it runs. An edit saves exactly as before.
// Mocked at the transport layer so the real `useConnector` query runs.
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: (...args) => mocks.enqueueSnackbar(...args),
}));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, post: mocks.post, patch: mocks.patch },
  endpoints: {
    falconAI: {
      connectors: "/falcon-ai/mcp-connectors/",
      connector: (id) => `/falcon-ai/mcp-connectors/${id}/`,
      connectorTools: (id) => `/falcon-ai/mcp-connectors/${id}/tools/`,
    },
  },
}));

const LIST_URL = "/falcon-ai/mcp-connectors/";
const CONNECTOR_URL = "/falcon-ai/mcp-connectors/conn-1/";
const NEW_URL = "/falcon-ai/mcp-connectors/conn-2/";

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

// A connector has no tools until they are discovered.
const NEW_ROW = {
  id: "conn-2",
  name: "Sentry",
  server_url: "https://mcp.sentry.dev/mcp",
  transport: "streamable_http",
  auth_type: "none",
  is_verified: false,
  tool_count: 0,
};

const NEW_DETAIL = { ...NEW_ROW, discovered_tools: [], enabled_tool_names: [] };

// The create route answers 201 with { status, result: <detail> }.
const CREATED = { data: { status: true, result: NEW_DETAIL } };

// DeepWiki after an edit renames it.
const RENAMED_ROW = { ...LIST_ROW, name: "DeepWiki Docs" };
const RENAMED_DETAIL = { ...DETAIL, name: "DeepWiki Docs" };

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

const listCalls = () =>
  mocks.get.mock.calls.filter(([url]) => url === LIST_URL).length;

// The first list read answers with `initial` at once. The reload after a save
// waits for `held.release()`, then answers with `reloaded`, or fails when it
// is an Error, so a test can look at the page while the reload is out.
// `details` is read on every request, so a test may change it mid-way.
const serve = ({ initial, reloaded, details = {} }) => {
  const held = {};
  let listReads = 0;
  mocks.get.mockImplementation((url) => {
    if (url === LIST_URL) {
      listReads += 1;
      if (listReads === 1) {
        return Promise.resolve({ data: { status: true, results: initial } });
      }
      return new Promise((resolve, reject) => {
        held.release = () =>
          reloaded instanceof Error
            ? reject(reloaded)
            : resolve({ data: { status: true, results: reloaded } });
      });
    }
    if (details[url]) {
      return Promise.resolve({ data: { result: details[url] } });
    }
    return Promise.resolve({ data: {} });
  });
  return held;
};

const fillNewConnector = () => {
  fireEvent.change(screen.getByPlaceholderText("e.g. Linear, GitHub"), {
    target: { value: NEW_ROW.name },
  });
  fireEvent.change(screen.getByPlaceholderText("https://mcp.example.com/mcp"), {
    target: { value: NEW_ROW.server_url },
  });
};

const expectCreatedToast = () =>
  expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
    "Connector created",
    expect.objectContaining({ variant: "success" }),
  );

describe("Connector settings — after save", () => {
  beforeEach(() => vi.clearAllMocks());

  it.each([
    // No connectors yet: the empty state's own call to action.
    ["the first connector", [], "Add connector"],
    // Beside an existing one: the header button.
    ["another connector", [LIST_ROW], "Add Connector"],
  ])(
    "opens %s once it is created and says so",
    async (_label, initial, addLabel) => {
      const held = serve({
        initial,
        reloaded: [...initial, NEW_ROW],
        details: { [NEW_URL]: NEW_DETAIL },
      });
      mocks.post.mockResolvedValue(CREATED);

      renderPage();
      // The add button renders only once the first list read has answered.
      fireEvent.click(await screen.findByRole("button", { name: addLabel }));
      fillNewConnector();
      fireEvent.click(screen.getByRole("button", { name: "Create" }));

      // The reload keeps the full-page loader, so nothing can be clicked, and
      // the create is confirmed while it runs.
      expect(
        await screen.findByRole("status", { name: "Loading" }),
      ).toBeInTheDocument();
      expect(screen.queryAllByRole("button")).toHaveLength(0);
      expect(listCalls()).toBe(2);
      expectCreatedToast();

      await act(async () => {
        held.release();
      });

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /Sentry/ })).toHaveClass(
          "Mui-selected",
        ),
      );
      // The new connector's own page: its details were read, and its status
      // and next steps show instead of "Select a connector".
      expect(await screen.findByText("No tools yet")).toBeInTheDocument();
      expect(mocks.get).toHaveBeenCalledWith(NEW_URL);
      expect(
        screen.getByRole("button", { name: "Test Connection" }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Discover Tools" }),
      ).toBeInTheDocument();
      expect(screen.queryByText("Select a connector")).not.toBeInTheDocument();
      expect(screen.queryByText("No connectors yet")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Create" }),
      ).not.toBeInTheDocument();

      expect(mocks.post).toHaveBeenCalledTimes(1);
      expect(mocks.post).toHaveBeenCalledWith(
        LIST_URL,
        expect.objectContaining({
          name: NEW_ROW.name,
          server_url: NEW_ROW.server_url,
        }),
      );
      expect(mocks.enqueueSnackbar).toHaveBeenCalledTimes(1);
    },
  );

  it("says it was created when the list cannot be reloaded, and opens nothing", async () => {
    const held = serve({
      initial: [LIST_ROW],
      reloaded: Object.assign(new Error("Network Error"), {
        transportCode: "ERR_NETWORK",
      }),
      details: { [NEW_URL]: NEW_DETAIL },
    });
    mocks.post.mockResolvedValue(CREATED);

    renderPage();
    fireEvent.click(
      await screen.findByRole("button", { name: "Add Connector" }),
    );
    fillNewConnector();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await screen.findByRole("status", { name: "Loading" });
    await act(async () => {
      held.release();
    });

    // The connector exists, so the confirmation stands; the list that would
    // carry it never arrived, so the selection is left as it was.
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to load connectors.",
    );
    expectCreatedToast();
    expect(await screen.findByText("Select a connector")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /DeepWiki/ })).not.toHaveClass(
      "Mui-selected",
    );
    expect(
      screen.queryByRole("button", { name: /Sentry/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create" }),
    ).not.toBeInTheDocument();
    expect(mocks.get).not.toHaveBeenCalledWith(NEW_URL);
  });

  it("keeps an edited connector open as before, with no created message", async () => {
    const details = { [CONNECTOR_URL]: DETAIL };
    const held = serve({
      initial: [LIST_ROW],
      reloaded: [RENAMED_ROW],
      details,
    });
    mocks.patch.mockImplementation(() => {
      details[CONNECTOR_URL] = RENAMED_DETAIL;
      return Promise.resolve({
        data: { status: true, result: RENAMED_DETAIL },
      });
    });

    renderPage();
    fireEvent.click(await screen.findByText("DeepWiki"));
    await screen.findByText("ask_question");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByPlaceholderText("e.g. Linear, GitHub"), {
      target: { value: "DeepWiki Docs" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByRole("status", { name: "Loading" });
    await act(async () => {
      held.release();
    });

    expect(
      await screen.findByRole("button", { name: "Edit" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getAllByText("DeepWiki Docs")).toHaveLength(2),
    );
    expect(screen.getByRole("button", { name: /DeepWiki Docs/ })).toHaveClass(
      "Mui-selected",
    );
    expect(screen.getByText("ask_question")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save" }),
    ).not.toBeInTheDocument();

    expect(mocks.patch).toHaveBeenCalledTimes(1);
    expect(mocks.patch).toHaveBeenCalledWith(
      CONNECTOR_URL,
      expect.objectContaining({ name: "DeepWiki Docs" }),
    );
    expect(mocks.post).not.toHaveBeenCalled();
    expect(mocks.enqueueSnackbar).not.toHaveBeenCalledWith(
      "Connector created",
      expect.anything(),
    );
  });

  it("keeps the form and its error when the create fails", async () => {
    serve({ initial: [LIST_ROW], reloaded: [LIST_ROW, NEW_ROW] });
    // Shape the axios interceptor rejects with when the network drops.
    mocks.post.mockRejectedValue({
      message: "Network Error",
      transportCode: "ERR_NETWORK",
    });

    renderPage();
    fireEvent.click(
      await screen.findByRole("button", { name: "Add Connector" }),
    );
    fillNewConnector();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Network Error");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Create" })).toBeEnabled(),
    );
    expect(screen.getByPlaceholderText("e.g. Linear, GitHub")).toHaveValue(
      NEW_ROW.name,
    );
    expect(
      screen.getByPlaceholderText("https://mcp.example.com/mcp"),
    ).toHaveValue(NEW_ROW.server_url);
    expect(listCalls()).toBe(1);
    expect(mocks.enqueueSnackbar).not.toHaveBeenCalled();
  });
});
