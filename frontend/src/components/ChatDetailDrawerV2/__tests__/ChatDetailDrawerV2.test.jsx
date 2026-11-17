/* eslint-disable react/prop-types */
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent } from "src/utils/test-utils";
import ChatDetailDrawerV2 from "../ChatDetailDrawerV2";

const traceDetail = vi.hoisted(() => ({ calls: [], result: undefined }));

vi.mock("src/api/project/trace-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useGetTraceDetail: (traceId, options) => {
    traceDetail.calls.push([traceId, options]);
    return { data: traceId ? traceDetail.result : undefined };
  },
}));

vi.mock("src/components/traceDetail/AddTagsPopover", () => ({
  default: ({ open, currentTags }) =>
    open ? (
      <div
        data-testid="add-tags-popover"
        data-current-tags={JSON.stringify(currentTags)}
      />
    ) : null,
}));

vi.mock("src/components/traceDetail/DrawerToolbar", () => ({
  default: () => <div data-testid="drawer-toolbar" />,
}));

vi.mock("../ChatLeftPanel", () => ({
  default: () => <div data-testid="chat-left-panel" />,
}));

vi.mock("../ChatRightPanel", () => ({
  default: ({ onAction }) => (
    <div data-testid="chat-right-panel">
      <button type="button" onClick={() => onAction?.("tags")}>
        Open tags action
      </button>
    </div>
  ),
}));

vi.mock("../Compare/ChatCompareView", () => ({
  default: () => <div data-testid="chat-compare-view" />,
}));

vi.mock("src/components/share-dialog", () => ({
  ShareDialog: ({ resourceType, resourceId }) => (
    <div
      data-testid="share-dialog"
      data-resource-type={resourceType}
      data-resource-id={resourceId}
    />
  ),
}));

vi.mock("src/api/project/saved-views", () => ({
  useGetSavedViews: () => ({ data: { custom_views: [] } }),
  useDeleteSavedView: () => ({ mutate: vi.fn() }),
  useReorderSavedViews: () => ({ mutate: vi.fn() }),
}));

vi.mock("src/components/imagine/useImagineStore", () => ({
  default: { getState: () => ({ reset: vi.fn() }) },
}));

const renderWithClient = (ui) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
};

describe("ChatDetailDrawerV2 share resource", () => {
  it("shares a simulation chat as its call execution", () => {
    renderWithClient(
      <ChatDetailDrawerV2
        data={{
          module: "simulate",
          origin: "simulate",
          id: "call-execution-1",
          simulation_call_type: "text",
        }}
        onClose={vi.fn()}
      />,
    );

    const dialog = screen.getByTestId("share-dialog");
    expect(dialog).toHaveAttribute("data-resource-type", "call_execution");
    expect(dialog).toHaveAttribute("data-resource-id", "call-execution-1");
  });

  it("shares a simulation chat by its call execution even when it has a trace", () => {
    renderWithClient(
      <ChatDetailDrawerV2
        data={{
          module: "simulate",
          id: "call-execution-1",
          trace_id: "trace-1",
        }}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByTestId("share-dialog")).toHaveAttribute(
      "data-resource-type",
      "call_execution",
    );
  });

  it("keeps sharing an observed chat as its trace", () => {
    renderWithClient(
      <ChatDetailDrawerV2
        data={{ module: "project", id: "trace-1", trace_id: "trace-1" }}
        onClose={vi.fn()}
      />,
    );

    const dialog = screen.getByTestId("share-dialog");
    expect(dialog).toHaveAttribute("data-resource-type", "trace");
    expect(dialog).toHaveAttribute("data-resource-id", "trace-1");
  });
});

// OBS-SW-132: Add tags saves the whole tag list, so it must start from the
// tags the chat's trace already has, or saving deletes them.
describe("ChatDetailDrawerV2 Add tags", () => {
  it("starts from an Observe chat's stored trace tags", async () => {
    const stored = [{ name: "vip", color: "#7857FC" }];
    traceDetail.calls = [];
    traceDetail.result = { trace: { tags: stored } };
    const user = userEvent.setup();

    renderWithClient(
      <ChatDetailDrawerV2
        data={{
          module: "project",
          id: "trace-1",
          trace_id: "trace-1",
          project_id: "project-1",
        }}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Open tags action" }));

    const popover = screen.getByTestId("add-tags-popover");
    expect(JSON.parse(popover.getAttribute("data-current-tags"))).toEqual(
      stored,
    );
    // The same arguments as ChatDetailsBar's tag row, so the read is shared.
    expect(traceDetail.calls).toContainEqual([
      "trace-1",
      { projectId: "project-1" },
    ]);
  });
});
