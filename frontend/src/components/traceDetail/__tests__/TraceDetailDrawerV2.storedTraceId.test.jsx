import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
import TraceDetailDrawerV2 from "../TraceDetailDrawerV2";

// Experiment results open a trace by the OpenTelemetry id the run recorded.
// The drawer opens it, and hands every part that reads or writes the trace
// (the detail read, share links, the annotation queue, tags and the
// annotation sidebar's scores), the span store's dashed id.

const DASHED = "4bf92f35-77b3-4da0-a3ce-929d0e0e4736";
const OPENTELEMETRY = "4bf92f3577b34da0a3ce929d0e0e4736";

const seen = vi.hoisted(() => ({
  detail: [],
  share: [],
  queue: [],
  tags: [],
  sources: [],
}));

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: (traceId) => {
    seen.detail.push(traceId);
    return { data: undefined, isLoading: true };
  },
}));
vi.mock("src/components/share-dialog", () => ({
  ShareDialog: ({ resourceId }) => {
    seen.share.push(resourceId);
    return null;
  },
}));
vi.mock(
  "src/sections/annotations/queues/components/add-to-queue-dialog",
  () => ({
    default: ({ sourceType, sourceIds }) => {
      seen.queue.push({ sourceType, sourceIds });
      return null;
    },
  }),
);
vi.mock("../AddTagsPopover", () => ({
  default: ({ traceId }) => {
    seen.tags.push(traceId);
    return null;
  },
}));
vi.mock("src/components/voiceAnnotationSources", async () => {
  const actual = await vi.importActual(
    "src/components/voiceAnnotationSources",
  );
  return {
    ...actual,
    buildTraceAnnotationSources: (args) => {
      seen.sources.push(args.traceId);
      return actual.buildTraceAnnotationSources(args);
    },
  };
});
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

const open = (traceId) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <TraceDetailDrawerV2
        open
        traceId={traceId}
        projectId="project-1"
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
  return () => {
    view.unmount();
    client.clear();
  };
};

const last = (calls) => calls[calls.length - 1];

describe("TraceDetailDrawerV2 opened by a run's OpenTelemetry trace id", () => {
  beforeEach(() => {
    Object.values(seen).forEach((calls) => calls.splice(0));
    vi.stubGlobal("localStorage", {
      getItem: vi.fn().mockReturnValue(null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it.each([OPENTELEMETRY, OPENTELEMETRY.toUpperCase(), DASHED])(
    "reads and writes the trace %s under its dashed id",
    (traceId) => {
      const close = open(traceId);

      expect(last(seen.detail)).toBe(DASHED);
      expect(last(seen.share)).toBe(DASHED);
      expect(last(seen.queue)).toEqual({
        sourceType: "trace",
        sourceIds: [DASHED],
      });
      expect(last(seen.tags)).toBe(DASHED);
      expect(last(seen.sources)).toBe(DASHED);
      close();
    },
  );

  it("opens an id in neither form as it is", () => {
    const close = open("trace-1");

    expect(last(seen.detail)).toBe("trace-1");
    expect(last(seen.share)).toBe("trace-1");
    close();
  });
});
