// @vitest-environment jsdom
// W9 A1, A6, A7 (W9-SCOPE §5): the organisation columns are a catalog of
// their own, all hidden by default, so the list's default view is the same;
// their cells show only what a row carries.
import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  DATASET_LIST_COLUMNS,
  DATASET_ORG_COLUMNS,
  datasetListCatalog,
  metadataKeyColumns,
} from "../../datasetList";
import { datasetOrgColumns } from "../orgColumns";
import { AgentsCell, FolderCell, MetaKeyCell, MetadataCell } from "../OrgCells";

const visible = (cols) =>
  cols.filter((c) => c.defaultVisible !== false).map((c) => c.id);

describe("catalog", () => {
  it("keeps the default visible set and hides every organisation column", () => {
    const all = datasetListCatalog(["domain", "team"]);
    expect(visible(all)).toEqual(visible(DATASET_LIST_COLUMNS));
    expect(all.slice(0, DATASET_LIST_COLUMNS.length)).toEqual([
      ...DATASET_LIST_COLUMNS,
    ]);
    expect(all.slice(DATASET_LIST_COLUMNS.length).map((c) => c.id)).toEqual([
      "folder",
      "agents",
      "metadata",
      "meta:domain",
      "meta:team",
    ]);
    expect(new Set(all.map((c) => c.id)).size).toBe(all.length);
  });

  it("one column per metadata key, once, sorted", () => {
    expect(
      metadataKeyColumns(["team", "domain", "team"]).map((c) => c.label),
    ).toEqual(["domain", "team"]);
  });

  it("every catalog column has a column def", () => {
    const defs = datasetOrgColumns({ metadataKeys: ["domain"] }).map(
      (c) => c.id,
    );
    expect(defs).toEqual([
      ...DATASET_ORG_COLUMNS.map((c) => c.id),
      "meta:domain",
    ]);
  });
});

describe("cells", () => {
  it("Metadata shows the first pair and +n", () => {
    render(
      <MetadataCell
        row={{ metadata: { team: "ops", domain: "billing", tier: 2 } }}
      />,
    );
    expect(screen.getByText("domain: billing")).toBeTruthy();
    expect(screen.getByText("+2")).toBeTruthy();
  });

  it("a metadata key, a folder and agents; — when there is none", () => {
    const { container } = render(
      <>
        <MetaKeyCell row={{ metadata: { tier: 2 } }} metaKey="tier" />
        <MetaKeyCell row={{ metadata: {} }} metaKey="tier" />
        <FolderCell row={{ folder: "support/billing" }} />
        <AgentsCell
          row={{
            agents: [
              {
                id: "g1",
                name: "Support Agent",
                last_run: { label: "#4A", display: "81.5%" },
              },
              { id: "g2", name: "Billing Agent", last_run: null },
            ],
          }}
        />
      </>,
    );
    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.getByText("support/billing")).toBeTruthy();
    expect(
      screen.getAllByTestId("agent-chip").map((c) => c.textContent),
    ).toEqual(["Support Agent · #4A 81.5%", "Billing Agent · no run yet"]);
    expect(container.textContent).toContain("—");
  });
});
