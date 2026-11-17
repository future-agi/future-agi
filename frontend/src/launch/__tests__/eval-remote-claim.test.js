import { afterEach, describe, expect, it } from "vitest";
import { absentTemplates } from "../absentPathGuard";
import { MADE_REAL, MADE_REAL_PATHS } from "../madeReal";
import { setLaunchOverrideForTest } from "../launchSwitch";

const PATHS = [
  "/model-hub/eval-templates/create-remote/",
  "/model-hub/eval-templates/{template_id}/remote/",
  "/model-hub/eval-templates/remote/test/",
  "/model-hub/eval-templates/{template_id}/remote/score-run/",
  "/model-hub/eval-templates/remote/jobs/{job_id}/",
];

afterEach(() => setLaunchOverrideForTest(null));

// Main ruling (t37): REMOTE-E2E-001 has no flow spec or passing walk yet, so
// the remote scorer and its five client paths stay unclaimed until it does.
describe("evals.remote endpoint claim (REMOTE-E2E-001)", () => {
  it("leaves the remote scorer and every client path unclaimed in EVAL", () => {
    expect(MADE_REAL.EVAL).not.toContain("evals.remote");
    for (const path of PATHS) expect(MADE_REAL_PATHS.EVAL).not.toContain(path);
  });

  it("refuses the five paths in a launch build until the walk passes", () => {
    setLaunchOverrideForTest({ launch: true });
    for (const path of PATHS) expect(absentTemplates()).toContain(path);
  });
});
