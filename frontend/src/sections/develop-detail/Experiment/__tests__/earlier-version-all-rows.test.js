// New experiment › an earlier dataset version runs on all of its rows. In a
// launch build the create copies the version whole and refuses one sent with
// picked rows, splits or a sample, so the Data step says so before Run. While
// the version picker is held (show-unreleased switch off), a version id that
// came with the form still blocks Run on the Data step.
import { afterEach, describe, expect, it } from "vitest";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { emptyExperimentValues } from "../experimentFormValues";
import { experimentBodyOfValues } from "../experimentBody";
import { serverExperimentBody } from "../serverBody";
import { dataIssues, stepIssues } from "../stepIssues";
import { EXPERIMENT_STEP } from "../constants";

const ALL_ROWS =
  "An earlier dataset version runs on all of its rows. Choose All rows and untick A random sample, or choose “Current rows” under Dataset version.";
const SWITCH_OFF_BLOCK =
  "A new experiment can't run with this dataset selection. Start a new experiment without it to use the dataset's current rows.";
const VERSION = "0b5b7b9e-8888-4c8f-9a8e-0e2f8d1d2c3a";
const LAUNCH = { earlierVersionAllRows: true };

const values = (over = {}) => ({
  ...emptyExperimentValues(),
  datasetVersionId: VERSION,
  ...over,
});

afterEach(() => setLaunchOverrideForTest(null));

describe("an earlier dataset version with a row selection, in a launch build", () => {
  it.each([
    ["picked rows", { rowIds: ["r1"] }],
    ["splits", { splitIds: ["s1"] }],
    ["a sample", { sample: { on: true, n: 2, seed: 7 } }],
    [
      "splits on a speech experiment",
      { experimentType: "tts", splitIds: ["s1"] },
    ],
  ])("%s with an earlier version is one Data step issue", (_l, over) => {
    expect(dataIssues(values(over), LAUNCH)).toEqual([ALL_ROWS]);
    const [, data] = stepIssues(values(over), LAUNCH);
    expect(data).toContain(ALL_ROWS);
  });

  it("an earlier version alone, or a selection on the current rows, has no issue", () => {
    expect(dataIssues(values(), LAUNCH)).toEqual([]);
    expect(
      dataIssues(values({ datasetVersionId: null, splitIds: ["s1"] }), LAUNCH),
    ).toEqual([]);
    expect(
      dataIssues(values({ datasetVersionId: "", rowIds: ["r1"] }), LAUNCH),
    ).toEqual([]);
  });

  it("outside a launch build, and on Edit, version and selection stay together", () => {
    expect(dataIssues(values({ splitIds: ["s1"] }))).toEqual([]);
    expect(
      dataIssues(values({ splitIds: ["s1"] }), { ...LAUNCH, isEditing: true }),
    ).toEqual([]);
  });
});

describe("a version id while the version picker is held (switch off)", () => {
  it("blocks Run on the Data step and is never sent, whatever the create contract declares", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: true,
      showUnreleased: false,
      madeReal: [],
    });
    const { blocks, body } = serverExperimentBody(
      experimentBodyOfValues(values(), { datasetId: "d1" }),
      { columns: [] },
    );
    const block = blocks.find((b) => b.field === "dataset_version_id");
    expect(block?.text).toBe(SWITCH_OFF_BLOCK);
    expect(block?.step).toBe(EXPERIMENT_STEP.DATA);
    expect(body).not.toHaveProperty("dataset_version_id");
  });
});
