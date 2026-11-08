import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, renderWithRouter, screen, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { endpoints } from "src/api/skills/endpoints";
import LabelHistoryDrawer from "./LabelHistoryDrawer";

vi.mock("src/utils/axios", () => ({ default: { get: vi.fn() } }));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ authenticated: true, user: { id: "reviewer" } }) }));
vi.mock("src/contexts/OrganizationContext", () => ({ useOrganization: () => ({ currentOrganizationId: "org-a", isReady: true }) }));
vi.mock("src/contexts/WorkspaceContext", () => ({ useWorkspace: () => ({ currentWorkspaceId: "ws-a", isReady: true }) }));
const headers = { "X-Organization-Id": "org-a", "X-Workspace-Id": "ws-a" };
vi.mock("src/components/iconify", () => ({ default: () => null }));
const skill = { id: "skill-1", name: "release-review" };
const events = [
  { label: "production", from_version: 2, to_version: null, actor: null, note: "Retired", created_at: "2026-11-03T12:00:00Z" },
  { label: "production", from_version: 1, to_version: 2, actor: { name: "Release bot", kind: "api_key" }, note: "Reviewed", created_at: "2026-11-02T12:00:00Z" },
  { label: "production", from_version: null, to_version: 1, actor: { name: "Test author", kind: "user" }, note: "Initial promotion", created_at: "2026-11-01T12:00:00Z" },
];
const response = (results = events, page = 1, count = 3) => ({ data: { result: { results, count, page, page_size: 25, total_pages: Math.ceil(count / 25) } } });
function show(props = {}, route = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const view = (overrides) => <QueryClientProvider client={client}><LabelHistoryDrawer open onClose={vi.fn()} skill={skill} {...overrides} /></QueryClientProvider>;
  const rendered = renderWithRouter(view(props), { route });
  return { ...rendered, client, update: (overrides) => rendered.rerender(view(overrides)) };
}
beforeEach(() => {
  vi.clearAllMocks();
  axios.get.mockResolvedValue(response());
});

describe("LabelHistoryDrawer", () => {
  it("renders assignment, promotion, removal and actors", async () => {
    const onClose = vi.fn();
    show({ label: "production", onClose });
    expect(await screen.findByText("production: v2 → Removed")).toBeInTheDocument();
    expect(screen.getByText("production: v1 → v2")).toBeInTheDocument();
    expect(screen.getByText("production: Not set → v1")).toBeInTheDocument();
    expect(screen.getByText(/Release bot \(API key\)/)).toBeInTheDocument();
    expect(screen.getByText(/Unknown actor/)).toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledWith(endpoints.labelHistory(skill.id), { params: { label: "production", page: 1, page_size: 25 }, signal: expect.any(AbortSignal), headers });
    fireEvent.click(screen.getByRole("button", { name: "Close label history" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("does not fetch history while closed", () => {
    show({ open: false });
    expect(axios.get).not.toHaveBeenCalled();
  });

  it("fetches the 26th event with the same label and distinct cached page, and returns to newer changes", async () => {
    const latest = Array.from({ length: 25 }, (_, index) => ({ ...events[0], note: `Recent change ${index}` }));
    axios.get.mockImplementation((_url, { params }) => Promise.resolve(response(params.page === 1 ? latest : [events[2]], params.page, 26)));
    const { client } = show({ label: "production" });
    await screen.findByText("Recent change 24");
    expect(screen.queryByText("Initial promotion")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Older changes" }));
    expect(await screen.findByText("Initial promotion")).toBeInTheDocument();
    expect(screen.queryByText("Recent change 24")).not.toBeInTheDocument();
    expect(axios.get).toHaveBeenLastCalledWith(endpoints.labelHistory(skill.id), { params: { label: "production", page: 2, page_size: 25 }, signal: expect.any(AbortSignal), headers });
    expect(client.getQueryCache().getAll().map((query) => query.queryKey)).toContainEqual(["skills", "v11", "org-a", "ws-a", "reviewer", "legacy", skill.id, "labelHistory", "production", { page: 2, pageSize: 25 }]);
    expect(screen.getByRole("button", { name: "Older changes" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Newer changes" }));
    expect(await screen.findByText("Recent change 24")).toBeInTheDocument();
  });

  it("shows a loading state, retries the failed older page and resets on close or label change", async () => {
    let rejectOlder;
    axios.get.mockImplementation((_url, { params }) => params.page === 1 ? Promise.resolve(response(events, 1, 26)) : new Promise((_resolve, reject) => { rejectOlder = reject; }));
    const view = show({ label: "production" });
    await screen.findByText("Retired");
    fireEvent.click(screen.getByRole("button", { name: "Older changes" }));
    expect(screen.getByRole("progressbar", { name: "Loading label history" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Older changes" })).toBeDisabled();
    await waitFor(() => expect(rejectOlder).toBeTypeOf("function"));
    rejectOlder(new Error("Offline"));
    await screen.findByText("Could not load label history");
    axios.get.mockImplementation((_url, { params }) => Promise.resolve(response(events, params.page, 26)));
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Retired");
    expect(axios.get.mock.lastCall[1].params).toMatchObject({ label: "production", page: 2 });
    view.update({ label: "staging" });
    await waitFor(() => expect(axios.get.mock.lastCall[1].params).toMatchObject({ label: "staging", page: 1 }));
    await screen.findByText("Retired");
    fireEvent.click(screen.getByRole("button", { name: "Older changes" }));
    await screen.findByText("Page 2 of 2");
    view.update({ open: false, label: "staging" });
    view.update({ open: true, label: "staging" });
    await screen.findByText("Page 1 of 2");
  });

  it.each(["loading", "error", "empty"])("shows the %s preview", (state) => {
    show({}, `/?state=${state}`);
    const text = { loading: "Loading label history", error: "Could not load label history", empty: "No label changes yet" };
    expect(state === "loading" ? screen.getByRole("progressbar", { name: text[state] }) : screen.getByText(text[state])).toBeInTheDocument();
  });
});
