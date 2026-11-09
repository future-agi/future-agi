import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import FalconAIFullPage from "src/sections/falcon-ai/FalconAIFullPage";
import FalconAISidebar from "src/sections/falcon-ai/FalconAISidebar";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import client, { endpoints } from "src/utils/axios";
import useFalconSocket from "src/sections/falcon-ai/hooks/useFalconSocket";
import {
  WorkspaceProvider,
  useWorkspace,
  writeSessionWorkspace,
} from "../WorkspaceContext";

const h = vi.hoisted(() => ({
  auth: { authenticated: true, loading: false, user: null },
  notify: vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => h.auth }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org" }),
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: h.notify }));
vi.mock("notistack", () => ({ enqueueSnackbar: h.notify }));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));
vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
vi.mock("src/api/contracts/openapi-contract", () => ({
  assertContractedRequestConfig: (x) => x,
  assertContractedResponse: (x) => x,
  findOpenApiEndpoint: vi.fn(),
}));
vi.mock("src/auth/context/jwt/utils", () => ({
  addToQueue: vi.fn(),
  clearTokens: vi.fn(),
  getIsRefreshing: vi.fn(),
  getRefreshToken: vi.fn(),
  getRememberMe: vi.fn(),
  processQueue: vi.fn(),
  refreshTokenRequest: vi.fn(),
  setIsRefreshing: vi.fn(),
  setSession: vi.fn(),
}));
vi.mock("src/sections/falcon-ai/hooks/useFalconContext", () => ({
  useFalconContext: () => ({ page: "falcon-ai" }),
}));
vi.mock("src/sections/falcon-ai/components/FalconAIHeader", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/ChatListPanel", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/SkillPicker", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/CustomizePanel", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/MessageList", () => ({
  default: function Messages() {
    return (
      <div data-testid="messages">
        {useFalconStore((s) => s.messages)
          .map((m) => m.content)
          .join("|")}
      </div>
    );
  },
}));
vi.mock("src/sections/falcon-ai/components/ChatInput", () => ({
  default: ({ onSend }) => (
    <button onClick={() => onSend("Explicit retry")}>Retry</button>
  ),
}));
function Location() {
  return <div data-testid="location">{useLocation().pathname}</div>;
}
const message =
  "The selected workspace is unavailable. Select an accessible workspace and retry.";
const adapter = vi.fn();
const oldAdapter = client.defaults.adapter;
let landing;
let finishRead;

beforeEach(() => {
  h.notify.mockClear();
  useFalconStore.getState().resetAll();
  sessionStorage.clear();
  sessionStorage.setItem("organizationId", "org");
  h.auth.user = { organization: { id: "org" }, default_workspace_id: "stale" };
  landing = {
    organization: { id: "org" },
    default_workspace_id: "landing",
    default_workspace_role: "Member",
    default_workspace_name: "Landing",
    ws_level: 3,
  };
  writeSessionWorkspace({
    id: "stale",
    orgId: "org",
    role: "Workspace Admin",
    wsLevel: 15,
  });
  client.defaults.headers.common["X-Organization-Id"] = "org";
  client.defaults.adapter = adapter;
  finishRead = undefined;
  adapter.mockReset().mockImplementation((config) => {
    if (config.url === endpoints.auth.me) {
      return new Promise((resolve) => {
        finishRead = () =>
          resolve({ status: 200, data: landing, headers: {}, config });
      });
    }
    return Promise.reject({
      config,
      response: {
        status: 403,
        data: { code: "workspace_unavailable", message },
        headers: {},
        config,
      },
    });
  });
});
afterEach(() => {
  cleanup();
  client.defaults.adapter = oldAdapter;
  delete client.defaults.headers.common["X-Workspace-Id"];
  delete client.defaults.headers.common["X-Organization-Id"];
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
const refuse = () =>
  client.post("/model-hub/datasets/create/", {}).catch((error) => error);

describe("HTTP workspace_unavailable recovery", () => {
  it.each(["fullpage", "sidebar"])(
    "resets stale chat on %s recovery and only an explicit retry creates in the landing",
    async (surface) => {
      const sockets = [];
      class OfflineSocket {
        static OPEN = 1;
        static CONNECTING = 0;
        readyState = 1;
        send = vi.fn();
        close() {
          this.readyState = 3;
        }
        constructor(url) {
          this.url = url;
          sockets.push(this);
        }
      }
      vi.stubGlobal("WebSocket", OfflineSocket);
      h.auth.user.accessToken = "offline";
      const recoveryAdapter = adapter.getMockImplementation();
      adapter.mockImplementation((config) => {
        if (config.url === endpoints.auth.me) return recoveryAdapter(config);
        const data =
          config.method === "post"
            ? { id: "new-conversation", title: "Explicit retry" }
            : config.url === endpoints.falconAI.conversation("old-conversation")
              ? {
                  messages: [
                    { id: "old", role: "user", content: "Private old message" },
                  ],
                }
              : [];
        return Promise.resolve({ status: 200, data, headers: {}, config });
      });
      useFalconStore.setState({
        currentConversationId: "old-conversation",
        messages: [{ id: "old", role: "user", content: "Private old message" }],
        isSidebarOpen: true,
        isStreaming: true,
        streamingMessageId: "old-placeholder",
        selectedContext: "old-scope",
        activeSkill: { id: "old-skill" },
        attachedFiles: [{ id: "old-file" }],
        pendingPrompt: "Do not replay",
        pendingNavigation: "/old-scope",
      });
      render(
        <WorkspaceProvider>
          <MemoryRouter
            initialEntries={[
              surface === "fullpage"
                ? "/dashboard/falcon-ai/old-conversation"
                : "/dashboard/overview",
            ]}
          >
            <Location />
            <Routes>
              <Route
                path="/dashboard/falcon-ai/:conversationId?"
                element={<FalconAIFullPage />}
              />
              <Route path="/dashboard/overview" element={<FalconAISidebar />} />
            </Routes>
          </MemoryRouter>
        </WorkspaceProvider>,
      );
      await waitFor(() =>
        expect(screen.getByTestId("messages").textContent).toContain(
          "Private old message",
        ),
      );
      await act(async () =>
        sockets.at(-1).onmessage({
          data: JSON.stringify({
            type: "error",
            data: {
              code: "workspace_unavailable",
              error: message,
              conversation_id: "old-conversation",
              client_message_id: "old-placeholder",
            },
          }),
        }),
      );
      await waitFor(() => expect(finishRead).toBeTypeOf("function"));
      await act(async () => finishRead());
      expect(useFalconStore.getState()).toMatchObject({
        currentConversationId: null,
        messages: [],
        isStreaming: false,
        streamingMessageId: null,
        selectedContext: "auto",
        activeSkill: null,
        attachedFiles: [],
        pendingPrompt: null,
        pendingNavigation: null,
      });
      if (surface === "fullpage")
        expect(screen.getByTestId("location").textContent).toBe(
          "/dashboard/falcon-ai",
        );
      else act(() => useFalconStore.getState().openSidebar());
      expect(screen.getByTestId("messages").textContent).toBe("");
      expect(sockets.at(-1).url).toContain("workspace_id=landing");
      for (const socket of sockets) expect(socket.send).not.toHaveBeenCalled();
      expect(
        adapter.mock.calls.filter(([c]) => c.method === "post"),
      ).toHaveLength(0);
      fireEvent.click(screen.getByText("Retry"));
      await waitFor(() => expect(sockets.at(-1).send).toHaveBeenCalledTimes(1));
      const creates = adapter.mock.calls.filter(([c]) => c.method === "post");
      expect(creates).toHaveLength(1);
      expect(creates[0][0].url).toBe(endpoints.falconAI.conversations);
      expect(creates[0][0].headers.get("X-Workspace-Id")).toBe("landing");
      expect(JSON.parse(sockets.at(-1).send.mock.calls[0][0])).toMatchObject({
        type: "chat",
        conversation_id: "new-conversation",
        message: "Explicit retry",
        file_ids: [],
        context: { page: "falcon-ai" },
      });
      expect(screen.getByTestId("messages").textContent).not.toContain(
        "Private old message",
      );
    },
  );

  it("recovers a socket refusal to the fresh landing and reconnects without replaying chat", async () => {
    const sockets = [];
    class OfflineSocket {
      static OPEN = 1;
      static CONNECTING = 0;
      readyState = 1;
      send = vi.fn();
      close() {
        this.readyState = 3;
      }
      constructor(url) {
        this.url = url;
        sockets.push(this);
      }
    }
    vi.stubGlobal("WebSocket", OfflineSocket);
    h.auth.user.accessToken = "offline";
    const { result } = renderHook(
      () => {
        useFalconSocket();
        return useWorkspace();
      },
      { wrapper: WorkspaceProvider },
    );
    expect(sockets.at(-1).url).toContain("workspace_id=stale");
    await act(async () =>
      sockets.at(-1).onmessage({
        data: JSON.stringify({
          type: "error",
          data: { code: "workspace_unavailable", error: message },
        }),
      }),
    );
    await waitFor(() => expect(finishRead).toBeTypeOf("function"));
    await act(async () => finishRead());
    expect(result.current.currentWorkspaceId).toBe("landing");
    expect(sockets.at(-1).url).toContain("workspace_id=landing");
    expect(adapter.mock.calls.map(([config]) => config.method)).toEqual([
      "get",
    ]);
    for (const socket of sockets) expect(socket.send).not.toHaveBeenCalled();
  });

  it("clears the stale selection, reads user-info once, lands, and never replays the write", async () => {
    const { result } = renderHook(() => useWorkspace(), {
      wrapper: WorkspaceProvider,
    });
    expect(result.current.currentWorkspaceId).toBe("stale");
    let error;
    await act(async () => {
      error = await refuse();
    });
    expect(error.code).toBe("workspace_unavailable");
    expect(sessionStorage.getItem("workspaceId")).toBeNull();
    expect(client.defaults.headers.common["X-Workspace-Id"]).toBeUndefined();
    await waitFor(() => expect(finishRead).toBeTypeOf("function"));
    const read = adapter.mock.calls.find(
      ([config]) => config.url === endpoints.auth.me,
    )[0];
    expect(read.headers.get("X-Workspace-Id")).toBeUndefined();
    await act(async () => finishRead());
    expect(result.current.currentWorkspaceId).toBe("landing");
    expect(result.current.currentWorkspaceRole).toBe("Member");
    expect(result.current.wsLevel).toBe(3);
    expect(sessionStorage.getItem("workspaceId")).toBe("landing");
    expect(client.defaults.headers.common["X-Workspace-Id"]).toBe("landing");
    expect(adapter.mock.calls.map(([config]) => config.method)).toEqual([
      "post",
      "get",
    ]);
    expect(h.notify).toHaveBeenCalledTimes(1);
    expect(h.notify.mock.calls[0][0]).toBe(message);
  });

  it("coalesces simultaneous refusals and supports a workspace-less landing", async () => {
    landing.default_workspace_id = null;
    const { result } = renderHook(() => useWorkspace(), {
      wrapper: WorkspaceProvider,
    });
    await act(async () => {
      await Promise.all([refuse(), refuse()]);
    });
    await waitFor(() => expect(finishRead).toBeTypeOf("function"));
    await act(async () => finishRead());
    expect(result.current.currentWorkspaceId).toBeNull();
    expect(result.current.isReady).toBe(true);
    expect(
      adapter.mock.calls.filter(([c]) => c.url === endpoints.auth.me),
    ).toHaveLength(1);
    expect(h.notify).toHaveBeenCalledTimes(1);
  });

  it("does not erase a newer selection when an old request is refused", async () => {
    renderHook(() => useWorkspace(), { wrapper: WorkspaceProvider });
    await act(async () => {
      await client
        .post(
          "/model-hub/datasets/create/",
          {},
          {
            headers: { "X-Workspace-Id": "previous-workspace" },
          },
        )
        .catch(() => {});
    });
    expect(sessionStorage.getItem("workspaceId")).toBe("stale");
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(h.notify).not.toHaveBeenCalled();
  });

  it("leaves ordinary permission failures alone", async () => {
    adapter.mockImplementation((config) =>
      Promise.reject({
        config,
        response: {
          status: 403,
          data: { code: "permission_denied" },
          headers: {},
          config,
        },
      }),
    );
    renderHook(() => useWorkspace(), { wrapper: WorkspaceProvider });
    await act(async () => {
      await refuse();
    });
    expect(sessionStorage.getItem("workspaceId")).toBe("stale");
    expect(adapter).toHaveBeenCalledTimes(1);
    expect(h.notify).not.toHaveBeenCalled();
  });
});
