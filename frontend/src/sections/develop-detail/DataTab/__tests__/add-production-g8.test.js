// v2.1 g8 (create:from-production): `?add=production` opens the production
// sheet on this dataset for an editor, refuses for a View-level member or a
// viewer, and waits while access loads.
import { describe, expect, it } from "vitest";
import { addParamAction } from "../dataAccessGate";

const EDIT = {
  view: true,
  download: true,
  edit: true,
  run: true,
  manage: false,
};
const VIEW = { ...EDIT, edit: false, run: false };

describe("add=production", () => {
  it("opens the sessions source on this dataset", () => {
    expect(
      addParamAction({ addParam: "production", datasetId: "ds-1", can: EDIT }),
    ).toEqual({
      kind: "open",
      request: { source: { kind: "sessions" }, target: { dataset_id: "ds-1" } },
    });
  });

  it("refuses read-only people, waits while access loads, ignores other values", () => {
    expect(
      addParamAction({ addParam: "production", datasetId: "ds-1", can: VIEW })
        .kind,
    ).toBe("refuse");
    expect(
      addParamAction({
        addParam: "production",
        datasetId: "ds-1",
        can: EDIT,
        roleViewer: true,
      }).kind,
    ).toBe("refuse");
    expect(
      addParamAction({
        addParam: "production",
        datasetId: "ds-1",
        can: EDIT,
        loading: true,
      }).kind,
    ).toBe("wait");
    expect(
      addParamAction({ addParam: "file", datasetId: "ds-1", can: EDIT }),
    ).toBeNull();
    expect(addParamAction({ addParam: "production", can: EDIT })).toBeNull();
  });
});
