import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import client from "src/utils/axios";
import AddTagsPopover from "../AddTagsPopover";
import { hashColor } from "../tagUtils";

// TH-8026 / GH #1378: adding a tag to selected calls failed with
// `tags.0: Not a valid string.` because trace tag PATCHes sent the UI's
// `{ name, color }` objects. These tests send the popover's requests through
// the real axios client (and its OpenAPI request guard) into a recording
// adapter, so they check the wire payload the backend receives.

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

const traceUuid = (n) =>
  `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

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
    data: { status: true, result: JSON.parse(config.data) },
  }));
  client.defaults.adapter = adapter;
});

afterEach(() => {
  client.defaults.adapter = originalAdapter;
});

const sentRequests = () =>
  adapter.mock.calls.map(([config]) => ({
    method: config.method,
    url: config.url,
    body: JSON.parse(config.data),
  }));

const renderPopover = (props) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const anchor = document.createElement("button");
  document.body.appendChild(anchor);
  return render(
    <QueryClientProvider client={queryClient}>
      <AddTagsPopover open anchorEl={anchor} onClose={() => {}} {...props} />
    </QueryClientProvider>,
  );
};

const addTag = async (name) => {
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText("Add tag..."), `${name}{Enter}`);
};

describe("AddTagsPopover trace tag payload", () => {
  it("tags 25 selected calls with a string-only payload", async () => {
    const calls = Array.from({ length: 25 }, (_, i) => ({
      id: traceUuid(i + 1),
      type: "trace",
      currentTags: [],
    }));
    renderPopover({ bulkItems: calls });

    await addTag("need improvement");

    await waitFor(() =>
      expect(snackbar).toHaveBeenCalledWith("Tags applied to 25 items", {
        variant: "success",
      }),
    );
    expect(snackbar).not.toHaveBeenCalledWith(
      "Failed to update tags",
      expect.anything(),
    );
    const requests = sentRequests();
    expect(requests).toHaveLength(25);
    requests.forEach((request, i) => {
      expect(request).toEqual({
        method: "patch",
        url: `/tracer/trace/${traceUuid(i + 1)}/tags/`,
        body: { tags: ["need improvement"] },
      });
    });
  });

  it("merges existing call tags as names and keeps span colours", async () => {
    const spanTags = [{ name: "slow", color: "#22C55E" }];
    renderPopover({
      bulkItems: [
        { id: traceUuid(1), type: "trace", currentTags: ["prod"] },
        {
          id: traceUuid(2),
          type: "trace",
          currentTags: [{ name: "vip", color: "#EF4444" }],
        },
        { id: "span-1", type: "span", currentTags: spanTags },
      ],
    });

    await addTag("need improvement");

    await waitFor(() =>
      expect(snackbar).toHaveBeenCalledWith("Tags applied to 3 items", {
        variant: "success",
      }),
    );
    expect(sentRequests()).toEqual([
      {
        method: "patch",
        url: `/tracer/trace/${traceUuid(1)}/tags/`,
        body: { tags: ["prod", "need improvement"] },
      },
      {
        method: "patch",
        url: `/tracer/trace/${traceUuid(2)}/tags/`,
        body: { tags: ["vip", "need improvement"] },
      },
      {
        // The span endpoint stores tag objects, colours included.
        method: "post",
        url: "/tracer/observation-span/update-tags/",
        body: {
          span_id: "span-1",
          tags: [
            ...spanTags,
            { name: "need improvement", color: hashColor("need improvement") },
          ],
        },
      },
    ]);
  });

  // The bulk bar offers Add tags for any selection, including one call. The
  // caller passes no traceId, so a one-item selection must still take the
  // bulk path (merge into that call's tags) rather than the single-trace one.
  it("tags exactly one selected call", async () => {
    renderPopover({
      bulkItems: [{ id: traceUuid(9), type: "trace", currentTags: ["prod"] }],
    });

    await addTag("need improvement");

    await waitFor(() =>
      expect(snackbar).toHaveBeenCalledWith("Tags applied to 1 item", {
        variant: "success",
      }),
    );
    expect(sentRequests()).toEqual([
      {
        method: "patch",
        url: `/tracer/trace/${traceUuid(9)}/tags/`,
        body: { tags: ["prod", "need improvement"] },
      },
    ]);
    expect(screen.getByText("Add tags to 1 item")).toBeInTheDocument();
  });

  it("saves a single trace's tags as names", async () => {
    renderPopover({
      traceId: traceUuid(7),
      currentTags: [{ name: "prod", color: "#3B82F6" }],
    });

    await addTag("need improvement");

    await waitFor(() =>
      expect(snackbar).toHaveBeenCalledWith("Tags updated", {
        variant: "success",
      }),
    );
    expect(sentRequests()).toEqual([
      {
        method: "patch",
        url: `/tracer/trace/${traceUuid(7)}/tags/`,
        body: { tags: ["prod", "need improvement"] },
      },
    ]);
  });
});
