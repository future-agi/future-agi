/* eslint-disable react/prop-types */
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent } from "src/utils/test-utils";
import VoiceDetailDrawerV2 from "../VoiceDetailDrawerV2";

vi.mock("src/components/traceDetail/DrawerToolbar", () => ({
  default: () => <div data-testid="drawer-toolbar" />,
}));

vi.mock("../VoiceLeftPanel", () => ({
  default: () => <div data-testid="voice-left-panel" />,
}));

vi.mock("../VoiceRightPanel", () => ({
  default: ({ onAction }) => (
    <button type="button" onClick={() => onAction("queue")}>
      Open queue action
    </button>
  ),
}));

vi.mock(
  "src/sections/annotations/queues/components/add-to-queue-dialog",
  () => ({
    default: ({ sourceType, sourceIds }) => (
      <div
        data-testid="add-to-queue-dialog"
        data-source-type={sourceType}
        data-source-ids={sourceIds.join(",")}
      />
    ),
  }),
);

vi.mock("src/components/share-dialog", () => ({
  ShareDialog: ({ open, resourceType, resourceId, fallbackShareUrl }) => (
    <div
      data-testid="share-dialog"
      data-open={String(open)}
      data-resource-type={resourceType}
      data-resource-id={resourceId}
      data-fallback-url={fallbackShareUrl || ""}
    />
  ),
}));

vi.mock("src/api/project/saved-views", () => ({
  useGetSavedViews: () => ({ data: { custom_views: [] } }),
  useDeleteSavedView: () => ({ mutate: vi.fn() }),
  useReorderSavedViews: () => ({ mutate: vi.fn() }),
}));

vi.mock("src/components/imagine/useImagineStore", () => ({
  default: {
    getState: () => ({ reset: vi.fn() }),
  },
}));

const renderWithClient = (ui) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
};

describe("VoiceDetailDrawerV2 queue source", () => {
  it("adds observed voice calls to queues as traces instead of spans", async () => {
    const user = userEvent.setup();

    renderWithClient(
      <VoiceDetailDrawerV2
        data={{
          module: "project",
          id: "call-execution-1",
          trace_id: "trace-1",
          status: "completed",
        }}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Open queue action" }));

    expect(screen.getByTestId("add-to-queue-dialog")).toHaveAttribute(
      "data-source-type",
      "trace",
    );
    expect(screen.getByTestId("add-to-queue-dialog")).toHaveAttribute(
      "data-source-ids",
      "trace-1",
    );
  });

  it("falls back to call_execution only when a voice call has no trace", async () => {
    const user = userEvent.setup();

    renderWithClient(
      <VoiceDetailDrawerV2
        data={{
          module: "simulate",
          id: "call-execution-1",
          status: "completed",
        }}
        onClose={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Open queue action" }));

    expect(screen.getByTestId("add-to-queue-dialog")).toHaveAttribute(
      "data-source-type",
      "call_execution",
    );
    expect(screen.getByTestId("add-to-queue-dialog")).toHaveAttribute(
      "data-source-ids",
      "call-execution-1",
    );
  });
});

describe("VoiceDetailDrawerV2 share resource", () => {
  it("shares an Observe voice call as its trace", () => {
    renderWithClient(
      <VoiceDetailDrawerV2
        data={{
          module: "project",
          id: "trace-1",
          trace_id: "trace-1",
          project_id: "project-1",
        }}
        onClose={vi.fn()}
      />,
    );

    const dialog = screen.getByTestId("share-dialog");
    expect(dialog).toHaveAttribute("data-resource-type", "trace");
    expect(dialog).toHaveAttribute("data-resource-id", "trace-1");
    expect(dialog.getAttribute("data-fallback-url")).toContain(
      "/dashboard/observe/project-1/voice/trace-1",
    );
  });

  it("shares a simulation voice call as its call execution", () => {
    renderWithClient(
      <VoiceDetailDrawerV2
        data={{
          module: "simulate",
          origin: "simulate",
          id: "call-execution-1",
          simulation_call_type: "voice",
        }}
        onClose={vi.fn()}
      />,
    );

    const dialog = screen.getByTestId("share-dialog");
    expect(dialog).toHaveAttribute("data-resource-type", "call_execution");
    expect(dialog).toHaveAttribute("data-resource-id", "call-execution-1");
    expect(dialog).toHaveAttribute("data-fallback-url", "");
  });

  it("shares a simulation call by its call execution even when it has a trace", () => {
    renderWithClient(
      <VoiceDetailDrawerV2
        data={{
          module: "simulate",
          id: "call-execution-1",
          trace_id: "trace-9",
          project_id: "project-1",
        }}
        onClose={vi.fn()}
      />,
    );

    const dialog = screen.getByTestId("share-dialog");
    expect(dialog).toHaveAttribute("data-resource-type", "call_execution");
    expect(dialog).toHaveAttribute("data-resource-id", "call-execution-1");
  });
});
