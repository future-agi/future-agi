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
