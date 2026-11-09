// DSX launch integration (the capability contract; inventory create:drawer,
// create:blank-grid, create:task-suite, create:from-production): the New
// dataset drawer of a launch build offers only the doors dev serves.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { launchRedirect } from "src/launch/launchRoutes";
import {
  IMPORT_OPTIONS,
  createDoorShown,
  offeredImportOptions,
} from "../importOptions";

const here = dirname(fileURLToPath(import.meta.url));
const src = (f) => readFileSync(join(here, "..", f), "utf8");
const ids = () => offeredImportOptions().map((o) => o.id);

describe("New dataset doors", () => {
  afterEach(() => setLaunchOverrideForTest(null));

  // DSX J09 feature-age ruling / release-lead 767/769/770 restores
  // the pre-existing HF (disabled) and manual-grid doors only.
  it("a launch build also offers the legacy HF and manual-grid doors", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: [],
    });
    expect(ids()).toEqual([
      "uploadFile",
      "manuallyCreateDataset",
      "addFromExistingDataset",
      "importFromHuggingface",
      "createSyntheticData",
    ]);
    expect(createDoorShown("importFromHuggingface")).toBe(true);
    expect(createDoorShown("taskSuite")).toBe(false);
    expect(createDoorShown("fromProduction")).toBe(false);
    expect(createDoorShown("manuallyCreateDataset")).toBe(true);
  });

  it("experimentsBasic ON changes nothing here", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      madeReal: [],
    });
    expect(ids()).toContain("manuallyCreateDataset");
    expect(ids()).not.toContain("taskSuite");
    expect(ids()).not.toContain("fromProduction");
    expect(ids()).toContain("importFromHuggingface");
  });

  it("the Hugging Face page remains reachable in a launch build", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: [],
    });
    for (const pathname of [
      "/dashboard/huggingface",
      "/dashboard/huggingface/",
    ])
      expect(launchRedirect({ pathname, search: "" }), pathname).toBeNull();
    setLaunchOverrideForTest({ launch: false, experimentsBasic: false });
    expect(
      launchRedirect({ pathname: "/dashboard/huggingface", search: "" }),
    ).toBeNull();
  });

  it("an L5 hf.create claim brings the door and the page back", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: ["hf.create"],
    });
    expect(ids()).toContain("importFromHuggingface");
    expect(
      launchRedirect({ pathname: "/dashboard/huggingface", search: "" }),
    ).toBeNull();
  });

  it("prototype builds and tests keep every door", () => {
    setLaunchOverrideForTest({ launch: false, experimentsBasic: false });
    expect(ids()).toEqual(IMPORT_OPTIONS.map((o) => o.id));
  });

  it("the drawer renders and opens only offered doors; the list's doors agree", () => {
    const drawer = src("AddDatasetDrawer/AddDatasetDrawer.jsx");
    expect(drawer).toMatch(/offeredImportOptions\(\)\.map\(\(option\)/);
    expect(drawer).toMatch(
      /if \(!OFFERED\.has\(id\) \|\| !createDoorShown\(id\)\) return;/,
    );
    const list = src("DevelopView.jsx");
    expect(list).toMatch(
      /canCreate &&\s*createDoorShown\("manuallyCreateDataset"\)/,
    );
  });
});
