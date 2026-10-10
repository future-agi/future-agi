import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
import { useTabStore } from "src/sections/projects/LLMTracing/tabStore";
import TraceDetailDrawerV2 from "../TraceDetailDrawerV2";

const displayPanelProps = vi.hoisted(() => ({ current: null }));

vi.mock("../TraceDisplayPanel", async () => {
  const actual = await vi.importActual("../TraceDisplayPanel");
  return {
    ...actual,
    default: (props) => {
      displayPanelProps.current = props;
      return null;
    },
  };
});
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

describe("TraceDetailDrawerV2 Save as new view", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn().mockReturnValue(null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    useTabStore.setState({ saveAsNewRequested: false });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("asks the tab bar for the personal save popover through the shared tab store", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { unmount } = render(
      <QueryClientProvider client={client}>
        <TraceDetailDrawerV2
          open
          traceId="trace-1"
          projectId="project-1"
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    act(() => displayPanelProps.current.onSaveAsNewView());

    expect(useTabStore.getState().saveAsNewRequested).toBe(true);
    unmount();
    client.clear();
  });
});
