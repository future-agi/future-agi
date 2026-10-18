import { describe, expect, it } from "vitest";

import {
  SIMULATION_PREVIEW_HTTP_TIMEOUT_MS,
  SimulationPreviewPageError,
  mergeSimulationPreviewPage,
  simulationPreviewRequestError,
} from "../simulation_preview_pagination";

const row = (id) => ({ id, status: "completed" });

describe("mergeSimulationPreviewPage", () => {
  it("appends a signed-snapshot continuation without hiding partial state", () => {
    const first = mergeSimulationPreviewPage({
      exact: true,
      results: [row("a"), row("b")],
      snapshot_total: 3,
      loaded_through: 2,
      has_more: true,
      complete: false,
      next_cursor: "signed-next",
      snapshot_at: "2026-08-14T00:00:00Z",
    });

    expect(first.items.map(({ id }) => id)).toEqual(["a", "b"]);
    expect(first.complete).toBe(false);
    expect(first.nextCursor).toBe("signed-next");

    const terminal = mergeSimulationPreviewPage(
      {
        exact: true,
        results: [row("c")],
        snapshot_total: 3,
        loaded_through: 3,
        has_more: false,
        complete: true,
        next_cursor: null,
        snapshot_at: "2026-08-14T00:00:00Z",
      },
      {
        previousItems: first.items,
        expectedSnapshotTotal: 3,
        expectedSnapshotAt: first.snapshotAt,
      },
    );

    expect(terminal.items.map(({ id }) => id)).toEqual(["a", "b", "c"]);
    expect(terminal.complete).toBe(true);
  });

  it.each([
    ["duplicate", [row("a")], [row("a")], 2, 2],
    ["skip", [row("a")], [row("c")], 3, 3],
  ])(
    "rejects a %s instead of publishing partial success",
    (_, previous, results, total, loaded) => {
      expect(() =>
        mergeSimulationPreviewPage(
          {
            exact: true,
            results,
            snapshot_total: total,
            loaded_through: loaded,
            has_more: false,
            complete: true,
            next_cursor: null,
            snapshot_at: "2026-08-14T00:00:00Z",
          },
          { previousItems: previous, expectedSnapshotTotal: total },
        ),
      ).toThrow(SimulationPreviewPageError);
    },
  );

  it("rejects terminal metadata while rows remain", () => {
    expect(() =>
      mergeSimulationPreviewPage({
        exact: true,
        results: [row("a")],
        snapshot_total: 2,
        loaded_through: 1,
        has_more: false,
        complete: true,
        next_cursor: null,
        snapshot_at: "2026-08-14T00:00:00Z",
      }),
    ).toThrow("terminal page is incomplete");
  });

  it("rejects an empty continuation page or stringified counts", () => {
    const base = {
      exact: true,
      results: [],
      snapshot_total: 2,
      loaded_through: 0,
      has_more: true,
      complete: false,
      next_cursor: "next",
      snapshot_at: "2026-08-14T00:00:00Z",
    };
    expect(() => mergeSimulationPreviewPage(base)).toThrow(
      "continuation page is empty",
    );
    expect(() =>
      mergeSimulationPreviewPage({
        ...base,
        results: [row("a")],
        snapshot_total: "2",
        loaded_through: 1,
      }),
    ).toThrow("invalid snapshot metadata");
  });

  it("rejects a continuation from a different snapshot timestamp", () => {
    expect(() =>
      mergeSimulationPreviewPage(
        {
          exact: true,
          results: [row("b")],
          snapshot_total: 2,
          loaded_through: 2,
          has_more: false,
          complete: true,
          next_cursor: null,
          snapshot_at: "2026-08-14T00:00:01Z",
        },
        {
          previousItems: [row("a")],
          expectedSnapshotTotal: 2,
          expectedSnapshotAt: "2026-08-14T00:00:00Z",
        },
      ),
    ).toThrow("timestamp changed");
  });
});

describe("simulationPreviewRequestError", () => {
  it("requires restart when continuation metadata fails closed", () => {
    expect(
      simulationPreviewRequestError(
        new SimulationPreviewPageError("The snapshot is inconsistent."),
      ),
    ).toEqual({
      message:
        "The snapshot is inconsistent. Restart the list to continue safely.",
      restartRequired: true,
    });
  });

  it("requires restart on a flattened 409 source drift", () => {
    expect(
      simulationPreviewRequestError({
        code: "simulation_preview_snapshot_changed",
        restart_required: true,
        statusCode: 409,
      }),
    ).toMatchObject({ restartRequired: true });
  });

  it("requires restart for a flattened invalid cursor instead of retrying it", () => {
    expect(
      simulationPreviewRequestError({
        code: "simulation_preview_cursor_invalid",
        restart_required: true,
        statusCode: 400,
      }),
    ).toMatchObject({ restartRequired: true });
  });

  it("makes a flattened 404 source terminal instead of retrying forever", () => {
    expect(
      simulationPreviewRequestError({
        code: "simulation_preview_not_found",
        statusCode: 404,
      }),
    ).toEqual({
      message:
        "This simulation preview source is no longer available. Select another simulation or execution.",
      restartRequired: false,
      terminal: true,
    });
  });

  it("explains a flattened transport timeout without claiming empty data", () => {
    expect(
      simulationPreviewRequestError({
        message: "Something went wrong",
        transportCode: "ECONNABORTED",
      }),
    ).toEqual({
      message: `The preview read exceeded ${
        SIMULATION_PREVIEW_HTTP_TIMEOUT_MS / 1_000
      } seconds. Retry when the data service is ready.`,
      restartRequired: false,
      terminal: false,
    });
  });

  it("also recognizes Axios' ETIMEDOUT timeout code", () => {
    expect(simulationPreviewRequestError({ code: "ETIMEDOUT" })).toMatchObject({
      restartRequired: false,
      message: expect.stringContaining(
        `exceeded ${SIMULATION_PREVIEW_HTTP_TIMEOUT_MS / 1_000} seconds`,
      ),
    });
  });

  it("continues to classify an unflattened Axios 404", () => {
    expect(
      simulationPreviewRequestError({
        response: { status: 404, data: { detail: "gone" } },
      }),
    ).toMatchObject({ terminal: true, restartRequired: false });
  });
});
