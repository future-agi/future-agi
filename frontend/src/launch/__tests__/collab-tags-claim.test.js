import { afterEach, describe, expect, it } from "vitest";
import { absentTemplates } from "../absentPathGuard";
import { SURFACE_BY_ID } from "../launchSurfaces";
import { setLaunchOverrideForTest } from "../launchSwitch";
import { MADE_REAL, MADE_REAL_PATHS } from "../madeReal";
import {
  DOOR_ROUTES,
  tagsOffered,
} from "src/sections/common/Collab/routeOffer";

// EXP-E2E-010: the server serves the workspace tag registry and an
// experiment's tags. The COLLAB claim backs the three paths, so a launch
// build calls them and the tag doors (picker, Manage tags, the Edit dialog's
// tags) come back.
const TAG_PATHS = [
  "/model-hub/tags/",
  "/model-hub/tags/{tag_id}/",
  "/model-hub/experiments/v2/{experiment_id}/tags/",
];

afterEach(() => setLaunchOverrideForTest(null));

describe("dataset.collab.tags by its COLLAB claim (EXP-E2E-010)", () => {
  it("is claimed in the COLLAB slot with the three tag paths", () => {
    expect(MADE_REAL.COLLAB).toContain("dataset.collab.tags");
    for (const path of TAG_PATHS) expect(MADE_REAL_PATHS.COLLAB).toContain(path);
    expect(TAG_PATHS).toContain(DOOR_ROUTES.tags);
    expect(TAG_PATHS).toContain(DOOR_ROUTES.experimentTags);
  });

  it("takes the tag paths off the absent list", () => {
    const absent = absentTemplates();
    for (const path of TAG_PATHS) expect(absent, path).not.toContain(path);
  });

  it("offers tags in a launch build", () => {
    setLaunchOverrideForTest({ launch: true });
    expect(tagsOffered()).toBe(true);
  });

  it("names the gate it has", () => {
    const { gate } = SURFACE_BY_ID["dataset.collab.tags"];
    expect(gate).toContain("tagsOffered()");
    expect(gate).not.toContain("NONE YET");
  });
});
