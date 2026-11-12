// v2.1 g8 (header:more): the dataset page's Delete hands the dataset to the
// list's one delete (counted confirm, hidden with a 10 s Undo).
import { describe, expect, it } from "vitest";
import { deleteHandoffState, handedDeleteOf } from "../deleteHandoff";
import { deleteConfirmCopy } from "../datasetList";

describe("header Delete handoff", () => {
  it("the page's state is what the list takes", () => {
    const state = deleteHandoffState({
      id: "ds-1",
      name: "Golden",
      numberOfExperiments: 3,
    });
    expect(handedDeleteOf(state)).toEqual([
      { id: "ds-1", name: "Golden", numberOfExperiments: 3 },
    ]);
  });

  it("nothing else is a delete", () => {
    expect(handedDeleteOf(null)).toBeNull();
    expect(handedDeleteOf({})).toBeNull();
    expect(handedDeleteOf({ deleteDatasets: [] })).toBeNull();
    expect(handedDeleteOf({ deleteDatasets: [{ name: "no id" }] })).toBeNull();
  });

  it("the confirm is the list's counted copy: typed name when experiments use it", () => {
    const [row] = deleteHandoffState({
      id: "ds-1",
      name: "Golden",
      numberOfExperiments: 3,
    }).deleteDatasets;
    const c = deleteConfirmCopy([row]);
    expect(c.title).toBe('Delete "Golden"?');
    expect(c.body).toMatch(/3 experiments/);
    expect(c.body).toMatch(/undo for 10 seconds/);
    expect(c.typedConfirm).toBe("Golden");
  });
});
