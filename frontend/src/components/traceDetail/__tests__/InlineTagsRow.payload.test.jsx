import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import client from "src/utils/axios";
import CallDetailsBar from "src/components/VoiceDetailDrawerV2/CallDetailsBar";
import ChatDetailsBar from "src/components/ChatDetailDrawerV2/ChatDetailsBar";
import SpanDetailPane from "../SpanDetailPane";

// TH-8026 / GH #1378: the inline tag rows on the call drawer, the chat drawer
// and the span pane's trace branch PATCH /tracer/trace/{id}/tags/, which only
// accepts tag names. Requests go through the real axios client (and its
// OpenAPI request guard) into a recording adapter.

const { snackbar } = vi.hoisted(() => ({ snackbar: vi.fn() }));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: snackbar,
}));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), error: vi.fn() },
}));
vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
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
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({ data: null }),
}));
vi.mock("src/components/ScoresListSection/ScoresListSection", () => ({
  default: () => <div data-testid="scores-list" />,
}));

const TRACE_ID = "00000000-0000-4000-8000-000000008026";
const TAGS_URL = `/tracer/trace/${TRACE_ID}/tags/`;

const adapter = vi.fn();
const originalAdapter = client.defaults.adapter;

beforeEach(() => {
  snackbar.mockReset();
  adapter.mockReset();
  adapter.mockImplementation(async (config) => ({
    status: 200,
    statusText: "OK",
    headers: {},
    config,
    data: {
      status: true,
      result: config.data ? JSON.parse(config.data) : {},
    },
  }));
  client.defaults.adapter = adapter;
});

afterEach(() => {
  client.defaults.adapter = originalAdapter;
});

const tagRequests = () =>
  adapter.mock.calls
    .map(([config]) => config)
    .filter((config) => config.url.includes("tags"))
    .map((config) => ({
      method: config.method,
      url: config.url,
      body: JSON.parse(config.data),
    }));

const renderWithClient = (ui) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
};

const addInlineTag = async (name) => {
  const user = userEvent.setup();
  await user.click(screen.getByText("tag"));
  await user.type(screen.getByPlaceholderText("tag name"), `${name}{Enter}`);
};

const removeInlineTag = async (name) => {
  const user = userEvent.setup();
  const chip = screen.getByText(name).parentElement;
  const close = chip.querySelector('[data-icon="mdi:close"]').closest("button");
  await user.click(close);
};

const expectTagPatch = async (tags) => {
  await waitFor(() =>
    expect(tagRequests()).toEqual([
      { method: "patch", url: TAGS_URL, body: { tags } },
    ]),
  );
  expect(snackbar).not.toHaveBeenCalledWith(
    "Failed to update tags",
    expect.anything(),
  );
};

const sites = [
  {
    name: "call drawer (CallDetailsBar)",
    render: (tags) =>
      renderWithClient(
        <CallDetailsBar
          data={{
            trace_id: TRACE_ID,
            module: "simulate",
            status: "completed",
            tags,
          }}
        />,
      ),
  },
  {
    name: "chat drawer (ChatDetailsBar)",
    render: (tags) =>
      renderWithClient(
        <ChatDetailsBar
          data={{
            trace_id: TRACE_ID,
            module: "simulate",
            status: "completed",
            tags,
          }}
        />,
      ),
  },
  {
    // Without a span id the pane's tag row writes to the trace instead.
    name: "span pane trace branch (SpanDetailPane)",
    render: (tags) =>
      renderWithClient(
        <SpanDetailPane
          entry={{ observation_span: { trace: TRACE_ID, name: "root", tags } }}
          projectId="project-1"
          onClose={() => {}}
        />,
      ),
  },
];

describe.each(sites)("$name inline trace tags", ({ render: renderSite }) => {
  it("adds a tag with a string-only payload", async () => {
    renderSite([{ name: "prod", color: "#3B82F6" }]);

    await addInlineTag("need improvement");

    await expectTagPatch(["prod", "need improvement"]);
  });

  it("removes a tag with a string-only payload", async () => {
    renderSite(["prod", { name: "vip", color: "#EF4444" }]);

    await removeInlineTag("prod");

    await expectTagPatch(["vip"]);
  });
});
