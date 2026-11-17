// Explore › Charts: a board card on a run metric. Where this build has no
// run metrics route, the card keeps its place on the board and says the
// shared line, not that the metric was removed, and nothing is asked. Not a
// launch build asks for the run metrics as before.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "src/utils/test-utils";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import ChartBoard from "../ChartBoard";

const h = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: h.get, put: h.put, post: h.post },
  };
});
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../BoardCardChart", () => ({ default: () => null }));
vi.mock("../charts/RunMetricChart", () => ({ default: () => null }));

const LINE = "Not available in this release";
const REMOVED = "This run metric was removed from the dataset.";
const BOARD = "rm:m1~bars~none~1";

let client;
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
const mount = () =>
  render(
    <QueryClientProvider client={client}>
      <ChartBoard
        value={BOARD}
        onChange={vi.fn()}
        datasetId="ds-1"
        runIds={["r1", "r2"]}
        columns={[]}
        measures={[]}
        groups={[]}
        onOpenRows={vi.fn()}
      />
    </QueryClientProvider>,
  );

beforeEach(() => {
  for (const fn of [h.get, h.put, h.post])
    fn.mockReset().mockImplementation(() => new Promise(() => {}));
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  setLaunchOverrideForTest(null);
});

describe("a run metric's board card", () => {
  it("where the route is not offered, keeps its place and says the line, not that the metric was removed", async () => {
    setLaunchOverrideForTest({ launch: true });
    mount();
    await settle();

    expect(screen.getAllByTestId("board-card")).toHaveLength(1);
    expect(screen.getByTestId("board-run-metric-off")).toHaveTextContent(LINE);
    expect(screen.queryByText(REMOVED)).toBeNull();
    expect(h.get).not.toHaveBeenCalled();
    expect(h.put).not.toHaveBeenCalled();
    expect(h.post).not.toHaveBeenCalled();
  });

  it("not a launch build, asks for the run metrics and shows no line", async () => {
    setLaunchOverrideForTest(null);
    mount();
    await settle();

    expect(screen.queryByTestId("board-run-metric-off")).toBeNull();
    expect(screen.queryByText(LINE)).toBeNull();
    expect(h.get.mock.calls.map(([path]) => path)).toContain(
      "/model-hub/datasets/ds-1/derived-metrics/",
    );
  });
});
