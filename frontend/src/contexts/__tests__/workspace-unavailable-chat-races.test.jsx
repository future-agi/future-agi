import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import client, { endpoints } from "src/utils/axios";
import FalconAIFullPage from "src/sections/falcon-ai/FalconAIFullPage";
import FalconAISidebar from "src/sections/falcon-ai/FalconAISidebar";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import { WorkspaceProvider, writeSessionWorkspace } from "../WorkspaceContext";
import { installWorkspaceSocketTransport } from "./helpers/workspaceUnavailableSocket";

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
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/falcon-ai/components/ContextSelector", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/SlashCommandPicker", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/SkillEditorDialog", () => ({
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
// ChatInput, AttachedFileChip, ChatListPanel, FalconAIHeader, send handlers,
// socket hook, API functions, recovery provider and store are all real.
const adapter = vi.fn();
const oldAdapter = client.defaults.adapter;
const file = {
  id: "upload-1",
  name: "notes.txt",
  size: 12,
  content_type: "text/plain",
  url: "/offline-file",
};
const conversation = {
  id: "B",
  title: "Private B conversation",
  created_at: "2026-11-01T12:00:00Z",
};
const history = {
  messages: [{ id: "b-message", role: "user", content: "Private B message" }],
};
let sockets;
let socketTransport;
let finishLanding;
let intercept;
function response(config, data) {
  return { status: 200, data, headers: {}, config };
}
function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
beforeEach(() => {
  useFalconStore.getState().resetAll();
  sessionStorage.clear();
  sessionStorage.setItem("organizationId", "org");
  h.auth.user = {
    organization: { id: "org" },
    default_workspace_id: "stale",
    accessToken: "offline",
  };
  writeSessionWorkspace({
    id: "stale",
    orgId: "org",
    role: "Workspace Admin",
    wsLevel: 15,
  });
  client.defaults.headers.common["X-Organization-Id"] = "org";
  client.defaults.adapter = adapter;
  socketTransport = installWorkspaceSocketTransport();
  sockets = socketTransport.sockets;
  finishLanding = undefined;
  intercept = () => undefined;
  adapter.mockReset().mockImplementation((config) => {
    if (config.url === endpoints.auth.me)
      return new Promise((resolve) => {
        finishLanding = () =>
          resolve(
            response(config, {
              organization: { id: "org" },
              default_workspace_id: "landing",
              default_workspace_role: "Member",
              ws_level: 3,
            }),
          );
      });
    const pending = intercept(config);
    if (pending) return pending;
    let data = [];
    if (config.url.startsWith(`${endpoints.falconAI.conversations}?`))
      data = { results: [conversation], has_more: false };
    else if (config.url === endpoints.falconAI.conversation("B"))
      data = history;
    else if (config.url.endsWith("stream-status/"))
      data = { result: { stream_status: "none" } };
    else if (config.method === "post")
      data = { id: "new-conversation", title: "Draft retry" };
    return Promise.resolve(response(config, data));
  });
});
afterEach(async () => {
  cleanup();
  // Drain already-started optional loaders before removing the offline adapter.
  await act(async () => {});
  client.defaults.adapter = oldAdapter;
  delete client.defaults.headers.common["X-Workspace-Id"];
  delete client.defaults.headers.common["X-Organization-Id"];
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function mount(surface = "fullpage", path) {
  if (surface === "sidebar") useFalconStore.getState().openSidebar();
  return render(
    <WorkspaceProvider>
      <MemoryRouter
        initialEntries={[
          path ||
            (surface === "sidebar"
              ? "/dashboard/overview"
              : "/dashboard/falcon-ai"),
        ]}
      >
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
}
async function recover(correlation = {}) {
  await act(async () =>
    sockets.at(-1).onmessage({
      data: JSON.stringify({
        type: "error",
        data: { code: "workspace_unavailable", ...correlation },
      }),
    }),
  );
  await waitFor(() => expect(finishLanding).toBeTypeOf("function"));
  await act(async () => finishLanding());
}
function expectEmpty() {
  expect(useFalconStore.getState()).toMatchObject({
    currentConversationId: null,
    messages: [],
    isStreaming: false,
    streamingMessageId: null,
  });
  expect(screen.getByTestId("messages").textContent).not.toContain(
    "Private B message",
  );
}
async function selectHistory(surface) {
  if (surface === "sidebar") fireEvent.click(screen.getByTitle("Chat history"));
  fireEvent.click(await screen.findByText("Private B conversation"));
}
describe("recovery with real chat controls", () => {
  it.each([
    ["fullpage", false],
    ["fullpage", true],
    ["sidebar", false],
    ["sidebar", true],
  ])(
    "restores the initial %s create refusal (attachments=%s) only as a draft",
    async (surface, attachments) => {
      intercept = (config) =>
        config.method === "post" &&
        config.headers.get("X-Workspace-Id") === "stale"
          ? Promise.reject({
              config,
              response: {
                status: 403,
                data: { code: "workspace_unavailable" },
              },
            })
          : undefined;
      mount(surface);
      await socketTransport.waitForSocket("stale");
      if (attachments)
        act(() => useFalconStore.getState().addAttachedFile(file));
      const input = screen.getByPlaceholderText("Message Falcon AI...");
      fireEvent.change(input, { target: { value: "Keep my first prompt" } });
      fireEvent.keyDown(input, { key: "Enter" });
      expect(input.value).toBe("");
      await waitFor(() => expect(finishLanding).toBeTypeOf("function"));
      await act(async () => finishLanding());
      if (surface === "sidebar")
        act(() => useFalconStore.getState().openSidebar());
      expectEmpty();
      expect(client.defaults.headers.common["X-Workspace-Id"]).toBe("landing");
      expect(screen.getByPlaceholderText("Message Falcon AI...").value).toBe(
        "Keep my first prompt",
      );
      expect(useFalconStore.getState().attachedFiles).toEqual(
        attachments ? [file] : [],
      );
      if (attachments)
        expect(screen.getByText("notes.txt")).toBeInTheDocument();
      expect(sockets.flatMap((s) => s.send.mock.calls)).toHaveLength(0);
      expect(
        adapter.mock.calls.filter(([c]) => c.method === "post"),
      ).toHaveLength(1);
      await socketTransport.waitForSocket("landing");
      fireEvent.keyDown(screen.getByPlaceholderText("Message Falcon AI..."), {
        key: "Enter",
      });
      await waitFor(() => expect(sockets.at(-1).send).toHaveBeenCalledTimes(1));
      expect(JSON.parse(sockets.at(-1).send.mock.calls[0][0])).toMatchObject({
        type: "chat",
        conversation_id: "new-conversation",
        message: "Keep my first prompt",
        file_ids: attachments ? [file.id] : [],
      });
      const creates = adapter.mock.calls.filter(([c]) => c.method === "post");
      expect(creates).toHaveLength(2);
      expect(creates.map(([c]) => c.headers.get("X-Workspace-Id"))).toEqual([
        "stale",
        "landing",
      ]);
      // The same submission, not a second draft, moves from create to socket.
      expect(useFalconStore.getState().pendingDraft.id).toBe(
        creates[1][0].falconSubmissionId,
      );
      const sent = JSON.parse(sockets.at(-1).send.mock.calls[0][0]);
      await recover({
        conversation_id: sent.conversation_id,
        client_message_id: sent.client_message_id,
      });
      if (surface === "sidebar")
        act(() => useFalconStore.getState().openSidebar());
      expect(screen.getByPlaceholderText("Message Falcon AI...").value).toBe(
        "Keep my first prompt",
      );
      expect(useFalconStore.getState().attachedFiles).toEqual(
        attachments ? [file] : [],
      );
      expect(useFalconStore.getState().pendingDraft).toBeNull();
      expect(sockets.flatMap((s) => s.send.mock.calls)).toHaveLength(1);
      expect(
        adapter.mock.calls.filter(([c]) => c.method === "post"),
      ).toHaveLength(2);
    },
  );

  it.each(["fullpage", "sidebar"])(
    "does not restore a newer %s submission for an older create refusal",
    async (surface) => {
      const pending = [];
      intercept = (config) =>
        config.method === "post"
          ? new Promise((resolve, reject) =>
              pending.push({ config, resolve, reject }),
            )
          : undefined;
      mount(surface);
      await socketTransport.waitForSocket("stale");
      const input = screen.getByPlaceholderText("Message Falcon AI...");
      for (const text of ["First submission", "Second submission"]) {
        fireEvent.change(input, { target: { value: text } });
        await act(async () => fireEvent.keyDown(input, { key: "Enter" }));
      }
      expect(pending).toHaveLength(2);
      await act(async () =>
        pending[0].reject({
          config: pending[0].config,
          response: { status: 403, data: { code: "workspace_unavailable" } },
        }),
      );
      await waitFor(() => expect(finishLanding).toBeTypeOf("function"));
      await act(async () => finishLanding());
      if (surface === "sidebar")
        act(() => useFalconStore.getState().openSidebar());
      await act(async () =>
        pending[1].resolve(response(pending[1].config, { id: "late" })),
      );
      expectEmpty();
      expect(screen.getByPlaceholderText("Message Falcon AI...").value).toBe(
        "",
      );
      expect(sockets.flatMap((s) => s.send.mock.calls)).toHaveLength(0);
    },
  );

  it.each(["fullpage", "sidebar"])(
    "clears an accepted initial %s submission before a later refusal",
    async (surface) => {
      mount(surface);
      await socketTransport.waitForSocket("stale");
      act(() => useFalconStore.getState().addAttachedFile(file));
      const input = screen.getByPlaceholderText("Message Falcon AI...");
      fireEvent.change(input, { target: { value: "Already accepted" } });
      fireEvent.keyDown(input, { key: "Enter" });
      await waitFor(() => expect(sockets.at(-1).send).toHaveBeenCalledTimes(1));
      const sent = JSON.parse(sockets.at(-1).send.mock.calls[0][0]);
      await act(async () =>
        sockets.at(-1).onmessage({
          data: JSON.stringify({
            type: "text_delta",
            data: {
              conversation_id: sent.conversation_id,
              message_id: "accepted",
              delta: "OK",
            },
          }),
        }),
      );
      expect(useFalconStore.getState().pendingDraft).toBeNull();
      await recover({
        conversation_id: sent.conversation_id,
        client_message_id: sent.client_message_id,
      });
      if (surface === "sidebar")
        act(() => useFalconStore.getState().openSidebar());
      expectEmpty();
      expect(screen.getByPlaceholderText("Message Falcon AI...").value).toBe(
        "",
      );
      expect(useFalconStore.getState().attachedFiles).toEqual([]);
      expect(sockets.flatMap((s) => s.send.mock.calls)).toHaveLength(1);
    },
  );

  it.each([
    ["fullpage", 500, "server_error"],
    ["sidebar", 500, "server_error"],
    ["fullpage", 403, "permission_denied"],
    ["sidebar", 403, "permission_denied"],
    ["fullpage", 500, "workspace_unavailable"],
    ["sidebar", 500, "workspace_unavailable"],
  ])(
    "never restores a %s create failure %s/%s into another workspace",
    async (surface, status, code) => {
      intercept = (config) =>
        config.method === "post"
          ? Promise.reject({ config, response: { status, data: { code } } })
          : undefined;
      mount(surface);
      await socketTransport.waitForSocket("stale");
      act(() => useFalconStore.getState().addAttachedFile(file));
      const input = screen.getByPlaceholderText("Message Falcon AI...");
      fireEvent.change(input, {
        target: { value: "Do not carry this failure" },
      });
      await act(async () => fireEvent.keyDown(input, { key: "Enter" }));
      expect(
        adapter.mock.calls.filter(([c]) => c.method === "post"),
      ).toHaveLength(1);
      // A different refusal can recover the workspace later, but cannot claim
      // this unsuccessful submission. A non-403 typed error may recover already.
      if (finishLanding) await act(async () => finishLanding());
      else await recover();
      if (surface === "sidebar")
        act(() => useFalconStore.getState().openSidebar());
      expect(client.defaults.headers.common["X-Workspace-Id"]).toBe("landing");
      expect(screen.getByPlaceholderText("Message Falcon AI...").value).toBe(
        "",
      );
      expect(useFalconStore.getState().attachedFiles).toEqual([]);
      expect(sockets.flatMap((s) => s.send.mock.calls)).toHaveLength(0);
      expectEmpty();
    },
  );

  it.each(["fullpage", "sidebar"])(
    "does not attach a pending %s create draft to an unrelated HTTP refusal",
    async (surface) => {
      const pending = deferred();
      intercept = (config) =>
        config.method === "post"
          ? pending.promise.then((data) => response(config, data))
          : undefined;
      mount(surface);
      await socketTransport.waitForSocket("stale");
      act(() => useFalconStore.getState().addAttachedFile(file));
      const input = screen.getByPlaceholderText("Message Falcon AI...");
      fireEvent.change(input, { target: { value: "Only my own refusal" } });
      await act(async () => fireEvent.keyDown(input, { key: "Enter" }));
      intercept = (config) =>
        config.method === "patch"
          ? Promise.reject({
              config,
              response: {
                status: 403,
                data: { code: "workspace_unavailable" },
              },
            })
          : undefined;
      await act(async () => {
        await client
          .patch(endpoints.falconAI.conversation("B"), {
            title: "Other request",
          })
          .catch(() => {});
      });
      await waitFor(() => expect(finishLanding).toBeTypeOf("function"));
      await act(async () => finishLanding());
      if (surface === "sidebar")
        act(() => useFalconStore.getState().openSidebar());
      await act(async () => pending.resolve({ id: "late-create" }));
      expectEmpty();
      expect(screen.getByPlaceholderText("Message Falcon AI...").value).toBe(
        "",
      );
      expect(useFalconStore.getState().attachedFiles).toEqual([]);
      expect(sockets.flatMap((s) => s.send.mock.calls)).toHaveLength(0);
    },
  );

  it.each([
    ["fullpage", false],
    ["fullpage", true],
    ["sidebar", false],
    ["sidebar", true],
  ])(
    "restores the refused %s draft (attachments=%s) without sending",
    async (surface, attachments) => {
      useFalconStore.setState({ currentConversationId: "B" });
      mount(surface);
      await socketTransport.waitForSocket("stale");
      if (attachments)
        act(() => useFalconStore.getState().addAttachedFile(file));
      const input = screen.getByPlaceholderText("Ask a follow-up...");
      fireEvent.change(input, { target: { value: "Keep my unsaved prompt" } });
      fireEvent.keyDown(input, { key: "Enter" });
      await waitFor(() => expect(sockets.at(-1).send).toHaveBeenCalledTimes(1));
      expect(input.value).toBe("");
      const sent = JSON.parse(sockets.at(-1).send.mock.calls[0][0]);
      await recover({
        conversation_id: "B",
        client_message_id: sent.client_message_id,
      });
      if (surface === "sidebar")
        act(() => useFalconStore.getState().openSidebar());
      expectEmpty();
      expect(screen.getByPlaceholderText("Message Falcon AI...").value).toBe(
        "Keep my unsaved prompt",
      );
      expect(useFalconStore.getState().attachedFiles).toEqual(
        attachments ? [file] : [],
      );
      if (attachments)
        expect(screen.getByText("notes.txt")).toBeInTheDocument();
      expect(sockets.flatMap((s) => s.send.mock.calls)).toHaveLength(1);
      expect(
        adapter.mock.calls.filter(([c]) => c.method === "post"),
      ).toHaveLength(0);
      await socketTransport.waitForSocket("landing");
      fireEvent.keyDown(screen.getByPlaceholderText("Message Falcon AI..."), {
        key: "Enter",
      });
      await waitFor(() => expect(sockets.at(-1).send).toHaveBeenCalledTimes(1));
      expect(JSON.parse(sockets.at(-1).send.mock.calls[0][0])).toMatchObject({
        type: "chat",
        conversation_id: "new-conversation",
        message: "Keep my unsaved prompt",
        file_ids: attachments ? [file.id] : [],
      });
      const creates = adapter.mock.calls.filter(([c]) => c.method === "post");
      expect(creates).toHaveLength(1);
      expect(creates[0][0].headers.get("X-Workspace-Id")).toBe("landing");
    },
  );

  it.each(["fullpage", "sidebar"])(
    "drops a pending %s history response after recovery",
    async (surface) => {
      const pending = deferred();
      intercept = (c) =>
        c.url === endpoints.falconAI.conversation("B")
          ? pending.promise.then((data) => response(c, data))
          : undefined;
      mount(surface);
      await socketTransport.waitForSocket("stale");
      await selectHistory(surface);
      await waitFor(() =>
        expect(
          adapter.mock.calls.some(
            ([c]) => c.url === endpoints.falconAI.conversation("B"),
          ),
        ).toBe(true),
      );
      await recover();
      if (surface === "sidebar")
        act(() => useFalconStore.getState().openSidebar());
      await act(async () => pending.resolve(history));
      expectEmpty();
      expect(
        adapter.mock.calls.some(([c]) => c.url.endsWith("stream-status/")),
      ).toBe(false);
    },
  );

  it("drops a pending stream-status completion after recovery", async () => {
    const pending = deferred();
    intercept = (c) =>
      c.url.endsWith("stream-status/")
        ? pending.promise.then((data) => response(c, data))
        : undefined;
    mount();
    await socketTransport.waitForSocket("stale");
    await selectHistory("fullpage");
    await waitFor(() =>
      expect(
        adapter.mock.calls.some(([c]) => c.url.endsWith("stream-status/")),
      ).toBe(true),
    );
    await recover();
    await act(async () =>
      pending.resolve({ result: { stream_status: "running" } }),
    );
    expectEmpty();
  });

  it.each(["fullpage", "sidebar"])(
    "drops a pending %s conversation-list response after recovery",
    async (surface) => {
      const pending = deferred();
      intercept = (c) =>
        c.url.startsWith(`${endpoints.falconAI.conversations}?`) &&
        c.headers.get("X-Workspace-Id") === "stale"
          ? pending.promise.then((data) => response(c, data))
          : undefined;
      mount(surface);
      await socketTransport.waitForSocket("stale");
      if (surface === "sidebar")
        fireEvent.click(screen.getByTitle("Chat history"));
      await waitFor(() =>
        expect(
          adapter.mock.calls.some(([c]) =>
            c.url.startsWith(`${endpoints.falconAI.conversations}?`),
          ),
        ).toBe(true),
      );
      await recover();
      if (surface === "sidebar")
        act(() => useFalconStore.getState().openSidebar());
      await act(async () =>
        pending.resolve({ results: [conversation], has_more: false }),
      );
      expect(useFalconStore.getState().conversations).toEqual([]);
      expect(
        screen.queryByText("Private B conversation"),
      ).not.toBeInTheDocument();
      expectEmpty();
    },
  );

  it("drops a pending header history list while the full-page header stays mounted", async () => {
    const pending = deferred();
    intercept = (c) =>
      c.url.startsWith(`${endpoints.falconAI.conversations}?`)
        ? pending.promise.then((data) => response(c, data))
        : undefined;
    mount();
    await socketTransport.waitForSocket("stale");
    fireEvent.click(screen.getByTitle("Chat history"));
    await waitFor(() =>
      expect(
        adapter.mock.calls.filter(([c]) =>
          c.url.startsWith(`${endpoints.falconAI.conversations}?`),
        ).length,
      ).toBeGreaterThanOrEqual(2),
    );
    await recover();
    await act(async () =>
      pending.resolve({ results: [conversation], has_more: false }),
    );
    expect(screen.queryAllByText("Private B conversation")).toHaveLength(0);
  });

  it.each(["fullpage", "sidebar"])(
    "keeps authorized %s history loading functional",
    async (surface) => {
      mount(surface);
      await socketTransport.waitForSocket("stale");
      await selectHistory(surface);
      await waitFor(() =>
        expect(useFalconStore.getState().messages[0]?.content).toBe(
          "Private B message",
        ),
      );
      expect(useFalconStore.getState().currentConversationId).toBe("B");
    },
  );

  it("drops events queued on the retired socket after recovery", async () => {
    mount();
    await socketTransport.waitForSocket("stale");
    const oldSocket = sockets.at(-1);
    await recover();
    await act(async () =>
      oldSocket.onmessage({
        data: JSON.stringify({
          type: "reconnect_status",
          data: { status: "running", conversation_id: "B" },
        }),
      }),
    );
    expectEmpty();
  });

  it("drops a pending deep-link load even if its ID is selected again after reset", async () => {
    const pending = deferred();
    intercept = (c) =>
      c.url === endpoints.falconAI.conversation("B")
        ? pending.promise.then((data) => response(c, data))
        : undefined;
    mount("fullpage", "/dashboard/falcon-ai/B");
    await socketTransport.waitForSocket("stale");
    await waitFor(() =>
      expect(
        adapter.mock.calls.some(
          ([c]) => c.url === endpoints.falconAI.conversation("B"),
        ),
      ).toBe(true),
    );
    await recover();
    act(() => useFalconStore.getState().setCurrentConversation("B"));
    await act(async () => pending.resolve(history));
    expect(useFalconStore.getState().messages).toEqual([]);
  });

  it.each(["fullpage", "sidebar"])(
    "does not send after a pending %s create completes across recovery",
    async (surface) => {
      const pending = deferred();
      intercept = (c) =>
        c.method === "post"
          ? pending.promise.then((data) => response(c, data))
          : undefined;
      mount(surface);
      await socketTransport.waitForSocket("stale");
      fireEvent.change(screen.getByPlaceholderText("Message Falcon AI..."), {
        target: { value: "Do not replay" },
      });
      fireEvent.keyDown(screen.getByPlaceholderText("Message Falcon AI..."), {
        key: "Enter",
      });
      await waitFor(() =>
        expect(adapter.mock.calls.some(([c]) => c.method === "post")).toBe(
          true,
        ),
      );
      await recover();
      if (surface === "sidebar")
        act(() => useFalconStore.getState().openSidebar());
      await act(async () => pending.resolve({ id: "B" }));
      expectEmpty();
      expect(sockets.flatMap((s) => s.send.mock.calls)).toHaveLength(0);
    },
  );
});
