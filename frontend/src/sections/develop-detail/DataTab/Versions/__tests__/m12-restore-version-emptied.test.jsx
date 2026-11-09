// Merge M12 (integration review 2, should-fix 1): after a column type change
// emptied values of v4 in every version, `Restore v4 as v7?` no longer says
// `Nothing leaves the history`; it names what stays empty (N4
// `emptied_by_type_change`). Without such a change the sentence is as before.
import React from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";

const changes = { current: null };
vi.mock("src/api/develop/dataset-versions", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useAsOfVersion: () => ({ latest: { id: "V6", number: 6 } }),
    useVersionChanges: () => ({ data: changes.current }),
    useSaveDatasetVersion: () => ({ mutate: () => {}, isPending: false }),
  };
});
vi.mock("../changeSetStore", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useChangeSet: () => ({ hasPending: false }) };
});
vi.mock("../useVersionUrl", () => ({
  default: () => ({ viewVersion: () => {} }),
}));

const theme = createTheme({ palette: palette("light") });
const V4 = { id: "V4", number: 4, row_count: 240 };
const COUNTS = { added: 0, modified: 3, deleted: 0, split_changes: 0 };

let Dialog = null;
let useRestoreDialog = null;
beforeAll(async () => {
  const mod = await import("../RestoreVersionDialog");
  Dialog = mod.default;
  useRestoreDialog = mod.useRestoreDialog;
}, 180000);
afterEach(() => {
  cleanup();
  act(() => useRestoreDialog?.getState().close());
});

const open = () => {
  render(
    <ThemeProvider theme={theme}>
      <Dialog datasetId="ds-1" />
    </ThemeProvider>,
  );
  act(() => useRestoreDialog.getState().open(V4));
};
const body = () => document.body.textContent;

describe("Restore version · values a column type change emptied", () => {
  it("names them instead of saying nothing leaves the history", () => {
    changes.current = {
      counts: COUNTS,
      status_rows: [],
      emptied_by_type_change: [
        {
          column_id: "c1",
          column_name: "customer_message",
          data_type: "document",
          values: 240,
        },
      ],
    };
    open();
    expect(body()).not.toContain("Nothing leaves the history");
    expect(body()).toContain(
      "v7 becomes the latest version, with the 240 rows of v4. v6 stays in the history, and experiments keep the version they ran on. 240 values of v4 in “customer_message” were emptied when its type changed to Document, so they stay empty.",
    );
  });

  it("keeps the sentence when no type change emptied anything", () => {
    changes.current = { counts: COUNTS, status_rows: [] };
    open();
    expect(body()).toContain(
      "Nothing leaves the history: v6 stays, and experiments keep the version they ran on.",
    );
  });
});
