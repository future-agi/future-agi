import { beforeEach, describe, expect, it, vi } from "vitest";

// A same-tab logout must hand the next person signed in on this tab the
// initial state of the stores that hold what the previous person was doing:
// the dataset version a grid shows, the pending change set, and the runs the
// tray waits on. Each case imports resetAllUserStores and the stores after
// vi.resetModules(), so both use the same store registry.

const DATASET = "dataset-of-user-a";

beforeEach(() => {
  vi.resetModules();
  window.sessionStorage.clear();
});

describe("logout resets the dataset Versions stores and the run tray", () => {
  it("puts the viewed version, the pending change set and the version dialogs back to their initial state", async () => {
    const { resetAllUserStores } = await import("src/utils/auth/clearStores");
    const { getAsOfView, useAsOfViewStore } = await import(
      "src/sections/develop-detail/DataTab/Versions/versionedSource"
    );
    const { applyOp, useChangeSetStore, useCreatedColumnsStore } = await import(
      "src/sections/develop-detail/DataTab/Versions/changeSetStore"
    );
    const { useRestoreDialog } = await import(
      "src/sections/develop-detail/DataTab/Versions/RestoreVersionDialog"
    );
    const { useVersionDialogs } = await import(
      "src/sections/develop-detail/DataTab/Versions/versionDialogsStore"
    );
    const initialView = getAsOfView(DATASET);

    useAsOfViewStore
      .getState()
      .setView(DATASET, { compare: "in", change: "modified", addedSince: 5 });
    useChangeSetStore.getState().setBaseVersion(DATASET, "version-5-of-user-a");
    useChangeSetStore.setState((s) => ({
      sets: {
        ...s.sets,
        [DATASET]: {
          ...s.sets[DATASET],
          ops: applyOp([], {
            op: "update_cell",
            row_id: "row-1",
            column_id: "column-1",
            value: "edit of user a",
            before: "saved value",
          }),
        },
      },
    }));
    useCreatedColumnsStore.getState().add(DATASET, {
      id: "column-of-user-a",
      name: "column of user a",
      from: "paste",
    });
    useRestoreDialog.getState().open({ id: "version-3-of-user-a", number: 3 });
    useVersionDialogs.getState().openSave(DATASET);

    expect(getAsOfView(DATASET).addedSince).toBe(5);
    expect(useChangeSetStore.getState().sets[DATASET].baseVersionId).toBe(
      "version-5-of-user-a",
    );
    expect(useChangeSetStore.getState().getOps(DATASET)).toHaveLength(1);
    expect(useCreatedColumnsStore.getState().of(DATASET)).toHaveLength(1);
    expect(useRestoreDialog.getState().version?.number).toBe(3);
    expect(useVersionDialogs.getState().save?.datasetId).toBe(DATASET);

    resetAllUserStores();

    expect(useAsOfViewStore.getState().views).toEqual({});
    expect(getAsOfView(DATASET)).toEqual(initialView);
    expect(useChangeSetStore.getState().sets).toEqual({});
    expect(useChangeSetStore.getState().getOps(DATASET)).toEqual([]);
    expect(useCreatedColumnsStore.getState().cols).toEqual({});
    expect(useRestoreDialog.getState().version).toBeNull();
    expect(useVersionDialogs.getState().save).toBeNull();
  });

  it("empties the run tray, including the runs it read from this tab's storage when the page loaded", async () => {
    window.sessionStorage.setItem(
      "fa.runTray",
      JSON.stringify([
        {
          runId: "run-1-of-user-a",
          experimentId: "experiment-of-user-a",
          datasetId: DATASET,
          label: "#1A",
          addedAt: 1,
        },
      ]),
    );
    const { resetAllUserStores } = await import("src/utils/auth/clearStores");
    const { RUN_TRAY_KEY, trackRuns, useRunTray } = await import(
      "src/sections/develop-detail/Experiment/runTrayStore"
    );

    expect(RUN_TRAY_KEY).toBe("fa.runTray");
    expect(useRunTray.getState().runs.map((e) => e.runId)).toEqual([
      "run-1-of-user-a",
    ]);

    trackRuns([{ run_id: "run-2-of-user-a", label: "#2A" }], {
      datasetId: DATASET,
      experimentId: "experiment-of-user-a",
    });
    useRunTray
      .getState()
      .setLive([{ run_id: "run-2-of-user-a", status: "Running" }]);
    useRunTray.getState().setCheck("experiment-of-user-a", { failed: true });

    expect(useRunTray.getState().runs).toHaveLength(2);
    expect(Object.keys(useRunTray.getState().live)).toEqual([
      "run-2-of-user-a",
    ]);
    expect(window.sessionStorage.getItem(RUN_TRAY_KEY)).toContain(
      "run-2-of-user-a",
    );

    resetAllUserStores();

    expect(useRunTray.getState().runs).toEqual([]);
    expect(useRunTray.getState().live).toEqual({});
    expect(useRunTray.getState().checks).toEqual({});
    expect(window.sessionStorage.getItem(RUN_TRAY_KEY) ?? "").not.toContain(
      "of-user-a",
    );
  });
});
