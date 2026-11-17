// The Row tab hands the Row view's runs count to the Generated section as it
// is: unknown stays unknown, 0 stays 0 and a count stays that count,
// whatever the row's own row_meta.counts says. A new or imported row, which
// the Row view gives a count of 0, keeps that 0.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { h } = vi.hoisted(() => ({ h: { fields: null } }));

// The Generated section's host: keep the props the Row tab hands it.
vi.mock("../RowFieldsTab", () => ({
  default: (props) => {
    h.fields = props;
    return null;
  },
}));
vi.mock("../RowRunsTab", () => ({ default: () => null }));
vi.mock("../RowAnnotationsTab", () => ({ default: () => null }));
vi.mock("../RowActivityTab", () => ({ default: () => null }));
vi.mock("src/sections/common/RowLayouts/RowLayoutTab", () => ({
  default: (props) => props.children ?? null,
}));
vi.mock("../RowViewStates", () => ({
  RowBanner: () => null,
  RowNotFound: () => null,
  RowSkeleton: () => null,
  deletedRowText: () => "",
}));
vi.mock("src/sections/develop-detail/states", () => ({
  useDevelopFilterStore: (select) => select({ resetFilters: () => {} }),
  useDevelopSearchStore: (select) => select({ setSearch: () => {} }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({ gridApi: { current: null } }),
}));
vi.mock("../../DataGrid/gridFilterActions", () => ({
  addColumnFilter: () => {},
}));

import RowViewBody from "../RowViewBody";

const MODEL = {
  input: [],
  expected: [],
  outputs: [],
  evals: [],
  human: [],
  metadata: [],
  noRole: [],
  rowMetadata: { value: {} },
};
const NAV = { notFound: false, isError: false, outside: false };
// The row's own row_meta names a count the Row view does not hand on.
const rowOf = (id) => ({ row_id: id, row_meta: { counts: { runs: 7 } } });

const mount = (
  runsCount,
  { rowId = "r1", isNew = false, isImported = false } = {},
) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <RowViewBody
        datasetId="ds-1"
        rowId={rowId}
        row={rowOf(rowId)}
        nav={NAV}
        model={MODEL}
        tab="row"
        setTab={() => {}}
        view={{ mode: "formatted" }}
        edit={{ canEdit: false }}
        actions={{ rerun: () => {}, rerunningId: null }}
        isNew={isNew}
        isImported={isImported}
        draftVersion={isImported ? 2 : null}
        runsCount={runsCount}
      />
    </QueryClientProvider>,
  );

beforeEach(() => {
  h.fields = null;
});
afterEach(() => cleanup());

describe("the Row tab hands the runs count on unchanged", () => {
  it.each([
    ["not known", undefined],
    ["0", 0],
    ["2", 2],
  ])("when it is %s", (_label, runsCount) => {
    mount(runsCount);
    expect(h.fields).not.toBeNull();
    expect(h.fields.runsCount).toBe(runsCount);
  });

  it.each([
    ["new-1", false],
    ["imp-1", true],
  ])("for %s, a row not on the server yet, hands on its 0", (id, imported) => {
    mount(0, { rowId: id, isNew: true, isImported: imported });
    expect(h.fields).not.toBeNull();
    expect(h.fields.runsCount).toBe(0);
  });
});
