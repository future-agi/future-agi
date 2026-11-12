import { useLayoutEffect } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useAnalyzeRunner } from "src/pages/dashboard/error-feed/useAnalyzeRunner";
import { useErrorFeedStore } from "src/pages/dashboard/error-feed/store";
import ImagineChatPanel from "src/components/imagine/ImagineChatPanel";
import ImagineTab from "src/components/imagine/ImagineTab";
import CommandPalette from "src/sections/common/CommandPalette/CommandPalette";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";
import useImagineStore from "src/components/imagine/useImagineStore";
import CustomizePanel from "src/sections/falcon-ai/components/CustomizePanel";
import FalconAIHeader from "src/sections/falcon-ai/components/FalconAIHeader";
import ChatInput from "src/sections/falcon-ai/components/ChatInput";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import client, { endpoints } from "src/utils/axios";
import FalconAIFullPage from "src/sections/falcon-ai/FalconAIFullPage";
import FalconAISidebar from "src/sections/falcon-ai/FalconAISidebar";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import {
  WorkspaceProvider,
  writeSessionWorkspace,
} from "src/contexts/WorkspaceContext";
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
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ onUpdate: vi.fn() }),
}));
vi.mock("src/hooks/useCapabilities", () => ({
  useFeatureAllowed: () => ({ allowed: true }),
}));
vi.mock("src/sections/common/CommandPalette/providers", () => ({
  datasetIdOfPath: () => null,
  useDatasetCommands: () => [],
  useDatasetKind: () => null,
  useGlobalCommands: () => [],
  useRowCommands: () => [],
  useRunCommands: () => [],
}));
vi.mock("src/components/imagine/WidgetCanvas", () => ({
  default: ({ emptyState }) => emptyState,
}));
vi.mock("src/api/project/saved-views", () => ({
  useCreateSavedView: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/sections/falcon-ai/components/ContextSelector", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/SlashCommandPicker", () => ({
  default: () => null,
}));
vi.mock("src/sections/falcon-ai/components/SkillEditorDialog", () => ({
  default: ({ onSaved }) => (
    <button onClick={onSaved}>Complete skill save</button>
  ),
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
  useFalconStore.setState({ skills: [], connectors: [] });
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
async function recoverHttp() {
  const reject = client.interceptors.request.use((config) => {
    if (config.method === "patch")
      config.adapter = () =>
        Promise.reject({
          config,
          response: { status: 403, data: { code: "workspace_unavailable" } },
        });
    return config;
  });
  try {
    await act(async () =>
      client
        .patch(endpoints.falconAI.conversation("B"), {
          title: "Unrelated edit",
        })
        .catch(() => {}),
    );
    await waitFor(() => expect(finishLanding).toBeTypeOf("function"));
    await act(async () => finishLanding());
    expect(client.defaults.headers.common["X-Workspace-Id"]).toBe("landing");
  } finally {
    client.interceptors.request.eject(reject);
  }
}
function expectEmpty() {
  expect(useFalconStore.getState()).toMatchObject({
    currentConversationId: null,
    messages: [],
    isStreaming: false,
    streamingMessageId: null,
    pendingDraft: null,
  });
}
function mountConsumer(element) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceProvider>
        <MemoryRouter>{element}</MemoryRouter>
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
}
function mountImagine() {
  useImagineStore.getState().reset();
  return render(
    <WorkspaceProvider>
      <MemoryRouter>
        <ImagineChatPanel
          traceId="old-workspace-trace"
          projectId="old-workspace-project"
        />
      </MemoryRouter>
    </WorkspaceProvider>,
  );
}
describe("deferred Imagine suggestion", () => {
  function mountTab(props = {}) {
    useImagineStore.getState().reset();
    return mountConsumer(
      <ImagineTab
        traceId="old-workspace-trace"
        projectId="old-workspace-project"
        suggestedPrompts={[
          { label: "Summarize the old trace", icon: "mdi:text" },
        ]}
        {...props}
      />,
    );
  }

  // Hold only the suggestion timer; HTTP promises, MUI and waitFor stay real.
  function holdSuggestion() {
    const realTimeout = window.setTimeout;
    const callbacks = [];
    vi.spyOn(window, "setTimeout").mockImplementation((fn, delay, ...args) => {
      if (delay === 100) {
        callbacks.push(fn);
        return realTimeout(() => {}, 60000);
      }
      return realTimeout(fn, delay, ...args);
    });
    return callbacks;
  }

  it("drops a suggestion scheduled before recovery, even if its callback was already queued", async () => {
    mountTab();
    const callbacks = holdSuggestion();
    fireEvent.click(screen.getByText("Summarize the old trace"));
    expect(callbacks).toHaveLength(1);
    await recoverHttp();
    await act(async () => callbacks[0]());
    expect(
      adapter.mock.calls.filter(([c]) => c.method === "post"),
    ).toHaveLength(0);
    expect(sockets.flatMap((socket) => socket.send.mock.calls)).toHaveLength(0);
    expectEmpty();
    expect(screen.getByTestId("messages").textContent).toBe("");
  });

  it("sends an authorized suggestion exactly once after mounting the hidden chat", async () => {
    mountTab({ readOnly: true });
    const callbacks = holdSuggestion();
    fireEvent.click(screen.getByText("Summarize the old trace"));
    await socketTransport.waitForSocket("stale");
    await act(async () => callbacks[0]());
    expect(
      adapter.mock.calls.filter(([c]) => c.method === "post"),
    ).toHaveLength(1);
    expect(sockets.flatMap((socket) => socket.send.mock.calls)).toHaveLength(1);
  });

  it("clears the scheduled suggestion on unmount", async () => {
    const view = mountTab();
    const callbacks = holdSuggestion();
    const clear = vi.spyOn(window, "clearTimeout");
    fireEvent.click(screen.getByText("Summarize the old trace"));
    const timer = window.setTimeout.mock.results.at(-1).value;
    view.unmount();
    expect(clear).toHaveBeenCalledWith(timer);
    await act(async () => callbacks[0]());
    expect(
      adapter.mock.calls.filter(([c]) => c.method === "post"),
    ).toHaveLength(0);
  });
});

describe("deferred command palette action", () => {
  it.each([false, true])(
    "recovery=%s scope-binds queued Ask Falcon",
    async (recover) => {
      useShortcutStore.getState().setPaletteOpen(true);
      mountConsumer(<CommandPalette />);
      fireEvent.change(screen.getByRole("combobox", { name: "Command" }), {
        target: { value: "zz old workspace prompt" },
      });
      const realTimeout = window.setTimeout;
      const queued = [];
      vi.spyOn(window, "setTimeout").mockImplementation(
        (fn, delay, ...args) => {
          if (delay === 0) {
            queued.push(fn);
            return realTimeout(() => {}, 60000);
          }
          return realTimeout(fn, delay, ...args);
        },
      );
      fireEvent.click(
        screen.getByText("Ask Falcon: “zz old workspace prompt”"),
      );
      expect(queued.length).toBeGreaterThan(0);
      window.setTimeout.mockRestore();
      if (recover) await recoverHttp();
      await act(async () => queued.forEach((callback) => callback()));
      expect(useFalconStore.getState().pendingPrompt).toBe(
        recover ? null : "zz old workspace prompt",
      );
      expect(useFalconStore.getState().isSidebarOpen).toBe(!recover);
    },
  );
});

describe("deferred analysis start effect", () => {
  it.each([false, true])(
    "reset before passive effect=%s rejects only retired pending starts",
    async (reset) => {
      function Runner() {
        useAnalyzeRunner("cluster-old", { project_id: "old-project" });
        useLayoutEffect(() => {
          if (reset) useFalconStore.getState().resetAll();
        }, []);
        return null;
      }
      client.defaults.headers.common["X-Workspace-Id"] = "stale";
      useErrorFeedStore.getState().setAnalyzePendingStart("cluster-old", true);
      mountConsumer(<Runner />);
      await act(async () => {});
      expect(
        adapter.mock.calls.filter(
          ([c]) =>
            c.method === "post" && c.url === endpoints.falconAI.conversations,
        ),
      ).toHaveLength(reset ? 0 : 1);
      await act(async () => useFalconStore.getState().resetAll());
    },
  );
});

describe("saved Imagine effect", () => {
  it("revalidates saved conversation props before a landing-scope remount can select them", async () => {
    useImagineStore.getState().reset();
    const tab = (
      <ImagineTab
        traceId="old-trace"
        projectId="old-project"
        savedConversationId="saved-old-conversation"
      />
    );
    const view = mountConsumer(tab);
    await waitFor(() =>
      expect(useFalconStore.getState().currentConversationId).toBe(
        "saved-old-conversation",
      ),
    );
    await recoverHttp();
    view.unmount();
    intercept = (c) =>
      c.url === endpoints.falconAI.conversation("saved-old-conversation")
        ? Promise.reject({ config: c, response: { status: 404, data: {} } })
        : undefined;
    mountConsumer(tab);
    await act(async () => {});
    expectEmpty();
    expect(useImagineStore.getState().conversationId).toBeNull();
  });

  it("drops a saved-conversation access read completed after recovery", async () => {
    useImagineStore.getState().reset();
    const pending = deferred();
    intercept = (c) =>
      c.url === endpoints.falconAI.conversation("saved-old-conversation")
        ? pending.promise.then(() =>
            response(c, { id: "saved-old-conversation" }),
          )
        : undefined;
    mountConsumer(
      <ImagineTab
        traceId="old-trace"
        projectId="old-project"
        savedConversationId="saved-old-conversation"
      />,
    );
    await act(async () => {});
    expect(useFalconStore.getState().currentConversationId).toBeNull();
    await recoverHttp();
    await act(async () => pending.resolve());
    expectEmpty();
  });
  it("does not rebind an old saved conversation when widgets change after recovery", async () => {
    useImagineStore.getState().reset();
    mountConsumer(
      <ImagineTab
        traceId="old-trace"
        projectId="old-project"
        savedConversationId="saved-old-conversation"
      />,
    );
    await waitFor(() =>
      expect(useFalconStore.getState().currentConversationId).toBe(
        "saved-old-conversation",
      ),
    );
    await recoverHttp();
    await act(async () =>
      useImagineStore
        .getState()
        .addWidget({ id: "late-widget", type: "markdown" }),
    );
    expectEmpty();
    expect(useImagineStore.getState().conversationId).toBeNull();
  });
});

describe("deferred skill refresh", () => {
  for (const surface of ["dialog", "inline"]) {
    it.each(["save", "delete"])(
      `${surface} drops a stale %s completion before refreshing shared skills`,
      async (action) => {
        const pending = deferred();
        const skill = {
          id: "private-skill",
          name: "Private skill",
          slug: "private",
          instructions: "Private workspace instructions",
          trigger_phrases: ["/private"],
        };
        intercept = (c) => {
          if (c.url === endpoints.falconAI.skill(skill.id)) {
            if (c.method !== "get")
              return pending.promise.then((data) => response(c, data));
            return Promise.resolve(response(c, skill));
          }
          if (c.url === endpoints.falconAI.skills)
            return Promise.resolve(response(c, { results: [skill] }));
          return undefined;
        };
        const saved = vi.fn();
        if (surface === "dialog") {
          const { default: Editor } = await vi.importActual(
            "src/sections/falcon-ai/components/SkillEditorDialog",
          );
          mountConsumer(
            <Editor open skill={skill} onSaved={saved} onClose={() => {}} />,
          );
        } else {
          mountConsumer(<CustomizePanel />);
          fireEvent.click(await screen.findByText("Private skill"));
          fireEvent.click(await screen.findByText("Edit skill"));
        }
        fireEvent.click(
          screen.getByRole("button", {
            name: action === "delete" ? "Delete" : "Save",
          }),
        );
        // Customize asks before deleting a skill; the request goes out when
        // the dialog is confirmed.
        if (surface === "inline" && action === "delete") {
          fireEvent.click(
            within(await screen.findByRole("dialog")).getByRole("button", {
              name: "Delete skill",
            }),
          );
        }
        await waitFor(() =>
          expect(
            adapter.mock.calls.some(
              ([c]) =>
                c.url === endpoints.falconAI.skill(skill.id) &&
                c.method !== "get",
            ),
          ).toBe(true),
        );
        await recoverHttp();
        const reads = adapter.mock.calls.filter(
          ([c]) => c.url === endpoints.falconAI.skills,
        ).length;
        await act(async () => pending.resolve({ result: skill }));
        expect(saved).not.toHaveBeenCalled();
        expect(
          adapter.mock.calls.filter(
            ([c]) => c.url === endpoints.falconAI.skills,
          ),
        ).toHaveLength(reads);
        expect(useFalconStore.getState().skills).toEqual([]);
      },
    );
  }
});

describe("landing catalog reload", () => {
  for (const surface of ["fullpage", "sidebar"]) {
    it.each(["skills", "connectors"])(
      `${surface} reloads the landing catalog once and drops old %s`,
      async (kind) => {
        const pending = deferred();
        intercept = (c) => {
          if (
            ![
              endpoints.falconAI.skills,
              endpoints.falconAI.connectors,
            ].includes(c.url)
          )
            return undefined;
          if (
            c.headers.get("X-Workspace-Id") === "stale" &&
            c.url === endpoints.falconAI[kind]
          ) {
            return pending.promise.then((data) => response(c, data));
          }
          const scope = c.headers.get("X-Workspace-Id");
          return Promise.resolve(
            response(c, {
              results: [{ id: scope, name: `${scope} catalog`, slug: scope }],
            }),
          );
        };
        mount(surface);
        await waitFor(() =>
          expect(
            adapter.mock.calls.some(
              ([c]) => c.url === endpoints.falconAI[kind],
            ),
          ).toBe(true),
        );
        await recoverHttp();
        await waitFor(() =>
          expect(useFalconStore.getState().connectors).toEqual([
            { id: "landing", name: "landing catalog", slug: "landing" },
          ]),
        );
        await act(async () =>
          pending.resolve({
            results: [{ id: "old", name: "Private old catalog", slug: "old" }],
          }),
        );
        expect(useFalconStore.getState().skills).toEqual([
          { id: "landing", name: "landing catalog", slug: "landing" },
        ]);
        expect(useFalconStore.getState().connectors).toEqual([
          { id: "landing", name: "landing catalog", slug: "landing" },
        ]);
        if (surface === "sidebar")
          await act(async () => useFalconStore.getState().openSidebar());
        expect(screen.queryAllByText("landing catalog").length).toBeGreaterThan(
          0,
        );
        expect(screen.queryByText("Private old catalog")).toBeNull();
        for (const endpoint of [
          endpoints.falconAI.skills,
          endpoints.falconAI.connectors,
        ]) {
          expect(
            adapter.mock.calls.filter(
              ([c]) =>
                c.url === endpoint &&
                c.headers.get("X-Workspace-Id") === "landing",
            ),
          ).toHaveLength(1);
        }
      },
    );
  }
});

describe("Imagine shared-store recovery", () => {
  it("keeps authorized Imagine create and send functional", async () => {
    mountImagine();
    await socketTransport.waitForSocket("stale");
    const input = screen.getByPlaceholderText("Message Falcon AI...");
    fireEvent.change(input, { target: { value: "Original workspace prompt" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(sockets.at(-1).send).toHaveBeenCalledTimes(1));
    expect(useFalconStore.getState().isStreaming).toBe(true);
  });
  it("does not send or resurrect streaming when an Imagine create completes after recovery", async () => {
    const pending = deferred();
    intercept = (c) =>
      c.method === "post"
        ? pending.promise.then((data) => response(c, data))
        : c.method === "patch"
          ? Promise.reject({
              config: c,
              response: {
                status: 403,
                data: { code: "workspace_unavailable" },
              },
            })
          : undefined;
    mountImagine();
    await socketTransport.waitForSocket("stale");
    const input = screen.getByPlaceholderText("Message Falcon AI...");
    fireEvent.change(input, { target: { value: "Original workspace prompt" } });
    await act(async () => fireEvent.keyDown(input, { key: "Enter" }));
    expect(
      adapter.mock.calls.filter(([c]) => c.method === "post"),
    ).toHaveLength(1);
    await act(async () =>
      client
        .patch(endpoints.falconAI.conversation("B"), {
          title: "Unrelated pending edit",
        })
        .catch(() => {}),
    );
    await waitFor(() => expect(finishLanding).toBeTypeOf("function"));
    await act(async () => finishLanding());
    expect(client.defaults.headers.common["X-Workspace-Id"]).toBe("landing");
    expect(useFalconStore.getState().currentConversationId).toBeNull();
    await act(async () =>
      pending.resolve({ id: "old-workspace-conversation" }),
    );
    const sends = sockets
      .flatMap((s) => s.send.mock.calls)
      .map(([p]) => JSON.parse(p));
    expectEmpty();
    expect(sends).toHaveLength(0);
    expect(useFalconStore.getState().isStreaming).toBe(false);
    expect(useFalconStore.getState().currentConversationId).toBeNull();
  });

  it.each(["selection", "workspace", "organization"])(
    "discards creation after a %s change",
    async (change) => {
      const pending = deferred();
      intercept = (c) =>
        c.method === "post"
          ? pending.promise.then((data) => response(c, data))
          : undefined;
      mountImagine();
      await socketTransport.waitForSocket("stale");
      fireEvent.change(screen.getByPlaceholderText("Message Falcon AI..."), {
        target: { value: "Old prompt" },
      });
      await act(async () =>
        fireEvent.keyDown(screen.getByPlaceholderText("Message Falcon AI..."), {
          key: "Enter",
        }),
      );
      await act(async () => {
        if (change === "selection")
          useFalconStore.getState().setCurrentConversation("new-selection");
        else
          client.defaults.headers.common[
            change === "workspace" ? "X-Workspace-Id" : "X-Organization-Id"
          ] = "new-scope";
        pending.resolve({ id: "old-conversation" });
      });
      expect(sockets.flatMap((s) => s.send.mock.calls)).toHaveLength(0);
      expect(useFalconStore.getState().messages).toEqual([]);
      expect(useFalconStore.getState().currentConversationId).toBe(
        change === "selection" ? "new-selection" : null,
      );
      expect(useImagineStore.getState().conversationId).toBeNull();
    },
  );

  it("does not reselect an existing Imagine conversation on recovery or remount", async () => {
    const view = mountImagine();
    await socketTransport.waitForSocket("stale");
    fireEvent.change(screen.getByPlaceholderText("Message Falcon AI..."), {
      target: { value: "First prompt" },
    });
    await act(async () =>
      fireEvent.keyDown(screen.getByPlaceholderText("Message Falcon AI..."), {
        key: "Enter",
      }),
    );
    expect(useFalconStore.getState().currentConversationId).toBe(
      "new-conversation",
    );
    await recoverHttp();
    expectEmpty();
    view.unmount();
    mountConsumer(
      <ImagineChatPanel traceId="landing-trace" projectId="landing-project" />,
    );
    expectEmpty();
    expect(sockets.flatMap((s) => s.send.mock.calls)).toHaveLength(1);
  });

  it("retains the refused initial draft and attachments without automatically sending", async () => {
    intercept = (c) =>
      c.method === "post"
        ? Promise.reject({
            config: c,
            response: { status: 403, data: { code: "workspace_unavailable" } },
          })
        : undefined;
    mountImagine();
    await socketTransport.waitForSocket("stale");
    await act(async () => useFalconStore.getState().addAttachedFile(file));
    fireEvent.change(screen.getByPlaceholderText("Message Falcon AI..."), {
      target: { value: "Keep this draft" },
    });
    await act(async () =>
      fireEvent.keyDown(screen.getByPlaceholderText("Message Falcon AI..."), {
        key: "Enter",
      }),
    );
    await waitFor(() => expect(finishLanding).toBeTypeOf("function"));
    await act(async () => finishLanding());
    expectEmpty();
    expect(screen.getByPlaceholderText("Message Falcon AI...").value).toBe(
      "Keep this draft",
    );
    expect(useFalconStore.getState().attachedFiles).toEqual([file]);
    expect(sockets.flatMap((s) => s.send.mock.calls)).toHaveLength(0);
  });
});

describe("shared-store asynchronous consumer census", () => {
  it.each(["select", "drop"])(
    "ignores stale %s uploads after recovery",
    async (method) => {
      const pending = deferred();
      intercept = (c) =>
        c.url === endpoints.falconAI.fileUpload
          ? pending.promise.then((data) => response(c, data))
          : undefined;
      const view = mountConsumer(<ChatInput />);
      const upload = new File(["old data"], "notes.txt", {
        type: "text/plain",
      });
      await act(async () => {
        if (method === "select")
          fireEvent.change(view.container.querySelector('input[type="file"]'), {
            target: { files: [upload] },
          });
        else
          fireEvent.drop(screen.getByPlaceholderText("Message Falcon AI..."), {
            dataTransfer: { files: [upload] },
          });
      });
      expect(
        adapter.mock.calls.filter(
          ([c]) => c.url === endpoints.falconAI.fileUpload,
        ),
      ).toHaveLength(1);
      await recoverHttp();
      await act(async () => pending.resolve({ status: true, result: file }));
      expect(useFalconStore.getState().attachedFiles).toEqual([]);
      expect(screen.queryByText("notes.txt")).toBeNull();
    },
  );

  it.each(["select", "drop"])(
    "keeps authorized %s uploads functional",
    async (method) => {
      intercept = (c) =>
        c.url === endpoints.falconAI.fileUpload
          ? Promise.resolve(response(c, { status: true, result: file }))
          : undefined;
      const view = mountConsumer(<ChatInput />);
      const upload = new File(["data"], "notes.txt", { type: "text/plain" });
      await act(async () => {
        if (method === "select")
          fireEvent.change(view.container.querySelector('input[type="file"]'), {
            target: { files: [upload] },
          });
        else
          fireEvent.drop(screen.getByPlaceholderText("Message Falcon AI..."), {
            dataTransfer: { files: [upload] },
          });
      });
      expect(useFalconStore.getState().attachedFiles).toEqual([file]);
    },
  );

  for (const surface of ["fullpage", "sidebar"]) {
    it.each(["skills", "connectors"])(
      `${surface} ignores stale %s and does not start dependent loads after recovery`,
      async (kind) => {
        const pending = deferred();
        intercept = (c) =>
          c.url === endpoints.falconAI[kind] &&
          c.headers.get("X-Workspace-Id") === "stale"
            ? pending.promise.then((data) => response(c, data))
            : undefined;
        mount(surface);
        await waitFor(() =>
          expect(
            adapter.mock.calls.some(
              ([c]) => c.url === endpoints.falconAI[kind],
            ),
          ).toBe(true),
        );
        await recoverHttp();
        const calls = adapter.mock.calls.length;
        await act(async () =>
          pending.resolve({
            results: [{ id: "old-scope-item", name: "Private old scope" }],
          }),
        );
        expect(useFalconStore.getState()[kind]).toEqual([]);
        expect(
          adapter.mock.calls
            .slice(calls)
            .filter(([c]) => c.url === endpoints.falconAI.connectors),
        ).toHaveLength(0);
      },
    );
    it(`${surface} keeps authorized skills and connectors loading`, async () => {
      intercept = (c) =>
        [endpoints.falconAI.skills, endpoints.falconAI.connectors].includes(
          c.url,
        ) && c.headers.get("X-Workspace-Id") === "stale"
          ? Promise.resolve(
              response(c, {
                results: [
                  { id: "available", name: "Available", slug: "available" },
                ],
              }),
            )
          : undefined;
      mount(surface);
      await waitFor(() =>
        expect(useFalconStore.getState().connectors).toHaveLength(1),
      );
      expect(useFalconStore.getState().skills).toHaveLength(1);
      await recoverHttp();
      expect(useFalconStore.getState()).toMatchObject({
        skills: [],
        connectors: [],
      });
    });
  }

  it.each(["customize", "header"])(
    "%s ignores a stale skill list completion",
    async (surface) => {
      const pending = deferred();
      intercept = (c) =>
        c.url === endpoints.falconAI.skills
          ? pending.promise.then((data) => response(c, data))
          : undefined;
      mountConsumer(
        surface === "customize" ? <CustomizePanel /> : <FalconAIHeader />,
      );
      if (surface === "header")
        fireEvent.click(screen.getByText("Complete skill save"));
      await waitFor(() =>
        expect(
          adapter.mock.calls.some(([c]) => c.url === endpoints.falconAI.skills),
        ).toBe(true),
      );
      await recoverHttp();
      await act(async () =>
        pending.resolve({
          results: [
            { id: "private", name: "Private old skill", slug: "private" },
          ],
        }),
      );
      expect(useFalconStore.getState().skills).toEqual([]);
      expect(screen.queryByText("Private old skill")).toBeNull();
    },
  );

  it.each(["customize", "header"])(
    "%s keeps authorized skill list completion",
    async (surface) => {
      intercept = (c) =>
        c.url === endpoints.falconAI.skills
          ? Promise.resolve(
              response(c, {
                results: [
                  { id: "allowed", name: "Allowed skill", slug: "allowed" },
                ],
              }),
            )
          : undefined;
      mountConsumer(
        surface === "customize" ? <CustomizePanel /> : <FalconAIHeader />,
      );
      if (surface === "header")
        fireEvent.click(screen.getByText("Complete skill save"));
      await waitFor(() =>
        expect(useFalconStore.getState().skills).toHaveLength(1),
      );
    },
  );
});
