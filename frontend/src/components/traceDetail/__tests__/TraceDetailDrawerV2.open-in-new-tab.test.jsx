import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
import TraceDetailDrawerV2 from "../TraceDetailDrawerV2";

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({ data: undefined, isLoading: true }),
}));
vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn().mockResolvedValue({ data: { result: [] } }),
    },
  };
});

describe("TraceDetailDrawerV2 open-in-new-tab integration option", () => {
  beforeEach(() =>
    vi.stubGlobal("localStorage", {
      getItem: vi.fn().mockReturnValue(null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    }),
  );
  afterEach(() => vi.unstubAllGlobals());
  it("preserves the real header action by default and hides only that action when requested", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const drawer = (hidden = false) => (
      <QueryClientProvider client={client}>
        <TraceDetailDrawerV2
          open
          traceId="trace-1"
          projectId="project-1"
          onClose={vi.fn()}
          {...(hidden ? { hideOpenInNewTab: true } : {})}
        />
      </QueryClientProvider>
    );
    const { rerender, unmount } = render(drawer());
    expect(
      screen.getByRole("button", { name: "Open in new tab" }),
    ).toBeInTheDocument();
    rerender(drawer(true));
    expect(
      screen.queryByRole("button", { name: "Open in new tab" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Fullscreen" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Download raw data" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Share trace" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Close (Esc)" }),
    ).toBeInTheDocument();
    rerender(drawer());
    expect(
      screen.getByRole("button", { name: "Open in new tab" }),
    ).toBeInTheDocument();
    unmount();
    client.clear();
  });

  it.each([false, true])(
    "keeps the trace inside its modal host only when disablePortal=%s",
    (disablePortal) => {
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const { unmount } = render(
        <QueryClientProvider client={client}>
          <div data-testid="trace-modal-host">
            <TraceDetailDrawerV2
              open
              traceId="trace-1"
              projectId="project-1"
              onClose={vi.fn()}
              disablePortal={disablePortal}
            />
          </div>
        </QueryClientProvider>,
      );
      const host = screen.getByTestId("trace-modal-host");
      const close = screen.getByRole("button", { name: "Close (Esc)" });
      expect(host.contains(close)).toBe(disablePortal);
      unmount();
      client.clear();
    },
  );
});
