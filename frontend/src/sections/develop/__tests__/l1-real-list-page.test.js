// L1 (the capability contract): what the Datasets list page offers on the real
// backend. A build that is not prototype mode (MODE production, as `yarn
// build` makes it) is the real set by default; the list's columns, its
// default sort and its row menu carry only what get-datasets and dev's
// writes serve.
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

/** Import a module as a non-prototype production build sees it. */
async function asProductionBuild(spec) {
  vi.resetModules();
  vi.stubEnv("MODE", "production");
  vi.stubEnv("VITE_ENVIRONMENT", "dev");
  vi.stubEnv("VITE_PROTOTYPE_MODE", "");
  return import(spec);
}

describe("L1 a production build is the real set by default", () => {
  it("switches every list extra and the versioned writes off", async () => {
    const caps = await asProductionBuild(
      "src/sections/develop/realBackendCaps",
    );
    expect(caps.isRealBackend()).toBe(true);
    expect(caps.datasetCaps()).toMatchObject({
      listQuery: "get-datasets",
      gridWrites: "classic",
      pendingChanges: false,
    });
    expect(caps.gridWritesClassic()).toBe(true);
  });

  it("stays the prototype set when prototype mode is on (local only)", async () => {
    vi.resetModules();
    vi.stubEnv("MODE", "development");
    vi.stubEnv("VITE_ENVIRONMENT", "local");
    vi.stubEnv("VITE_PROTOTYPE_MODE", "true");
    const caps = await import("src/sections/develop/realBackendCaps");
    expect(caps.isRealBackend()).toBe(false);
    expect(caps.datasetCaps().listQuery).toBe("w9-query");
  });

  it("opens the list newest first (get-datasets has no updated_at)", async () => {
    const list = await asProductionBuild("src/sections/develop/datasetList");
    expect(list.LIST_URL_DEFAULTS.sort).toBe("created_at:desc");
    expect(list.sortingFromParam(list.LIST_URL_DEFAULTS.sort)).toEqual([
      { id: "createdAt", desc: true },
    ]);
  });
});

describe("L1 the list's columns on the real backend", () => {
  it("lists only what get-datasets answers, Name · Rows · Created shown", async () => {
    const { datasetListCatalog } = await import(
      "src/sections/develop/datasetList"
    );
    const { setLaunchOverrideForTest } = await import(
      "src/launch/launchSwitch"
    );
    setLaunchOverrideForTest({ launch: true, madeReal: [] });
    const real = datasetListCatalog(["domain"], true);
    expect(real.map((c) => c.id)).toEqual([
      "name",
      "numberOfDatapoints",
      "numberOfExperiments",
      "createdAt",
      "numberOfOptimisations",
    ]);
    expect(
      real.filter((c) => c.defaultVisible !== false).map((c) => c.id),
    ).toEqual(["name", "numberOfDatapoints", "createdAt"]);
    // The prototype catalog is unchanged: run columns, org and metadata.
    const proto = datasetListCatalog(["domain"], false).map((c) => c.id);
    for (const id of [
      "lastExperiment",
      "baseline",
      "bestRun",
      "updatedAt",
      "folder",
      "meta:domain",
    ])
      expect(proto).toContain(id);
    setLaunchOverrideForTest(null);
  });

  it("has no star in the Name cell and a plain Experiments count", async () => {
    const { setLaunchOverrideForTest } = await import(
      "src/launch/launchSwitch"
    );
    setLaunchOverrideForTest({ launch: true, madeReal: [] });
    const { datasetListColumns } = await import(
      "src/sections/develop/datasetListColumns"
    );
    const cols = datasetListColumns({
      query: "",
      rowActions: {},
      canEdit: true,
      canDelete: true,
      draggable: true,
      real: true,
    });
    // Only what get-datasets answers, and the ⋯ column (the table renders
    // every def it gets, whatever the layout's catalog holds).
    expect(cols.map((c) => c.id)).toEqual([
      "name",
      "numberOfDatapoints",
      "numberOfExperiments",
      "createdAt",
      "numberOfOptimisations",
      "actions",
    ]);
    const proto = datasetListColumns({
      query: "",
      rowActions: {},
      canEdit: true,
      canDelete: true,
      real: false,
    }).map((c) => c.id);
    for (const id of ["lastExperiment", "baseline", "bestRun", "updatedAt"])
      expect(proto).toContain(id);
    const rows = cols.find((c) => c.id === "numberOfDatapoints");
    expect(rows.description).toBe("Rows in this dataset");
    const name = cols.find((c) => c.id === "name");
    const el = name.cell({ row: { original: { id: "d", name: "n" } } });
    expect(el.props.star).toBe(false);
    expect(el.props.draggable).toBe(false);
    const exp = cols.find((c) => c.id === "numberOfExperiments");
    const cell = exp.cell({ row: { original: {} }, getValue: () => 2 });
    expect(cell.props.value).toBe(2);
    setLaunchOverrideForTest({
      launch: true,
      madeReal: ["list.organize.folders"],
    });
    const folderCell = datasetListColumns({
      real: true,
      rowActions: {},
      draggable: true,
    })
      .find((column) => column.id === "name")
      .cell({ row: { original: { id: "d", name: "n" } } });
    expect(folderCell.props.draggable).toBe(false);
    expect(folderCell.props.star).toBe(false);
    setLaunchOverrideForTest(null);
  });
});

describe("L1 the row menu shows only the actions the host passes", () => {
  it("drops items without an action and tidies dividers", async () => {
    const { tidyMenu } = await import("src/sections/develop/datasetList");
    const f = () => {};
    const items = [
      { label: "Open", run: f },
      { label: "Open experiments", run: undefined },
      { divider: true, key: "d1" },
      { label: "Edit details…", run: undefined },
      { label: "Duplicate", run: f },
      { label: "Download…", run: undefined },
      { divider: true, key: "d2" },
      { divider: true, key: "d3" },
      { label: "Delete…", run: f },
      { divider: true, key: "d4" },
    ];
    expect(tidyMenu(items).map((i) => i.label ?? i.key)).toEqual([
      "Open",
      "d1",
      "Duplicate",
      "d2",
      "Delete…",
    ]);
  });

  it("names exactly the real row actions", async () => {
    const { REAL_ROW_ACTIONS } = await import(
      "src/sections/develop/realBackendCaps"
    );
    expect([...REAL_ROW_ACTIONS].sort()).toEqual(
      ["copyId", "copyLink", "download", "duplicate", "open", "remove"].sort(),
    );
  });
});
