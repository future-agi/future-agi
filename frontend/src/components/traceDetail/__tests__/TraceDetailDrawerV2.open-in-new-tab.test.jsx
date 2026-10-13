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
      screen.getByLabelText("Open in new tab").querySelector("button"),
    ).toBeInTheDocument();
    rerender(drawer(true));
    expect(screen.queryByLabelText("Open in new tab")).not.toBeInTheDocument();
    expect(
      screen.getByLabelText("Fullscreen").querySelector("button"),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("Download raw data").querySelector("button"),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("Share trace").querySelector("button"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Close (Esc)" }),
    ).toBeInTheDocument();
    rerender(drawer());
    expect(
      screen.getByLabelText("Open in new tab").querySelector("button"),
    ).toBeInTheDocument();
    unmount();
    client.clear();
  });
});
