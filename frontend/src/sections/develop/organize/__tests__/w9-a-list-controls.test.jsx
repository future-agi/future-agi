// @vitest-environment jsdom
// W9 A2, A3, A5 (W9-SCOPE §5): the list's Filter / Group by controls and
// chips: fields the list answers + metadata keys, agents named, left-out
// sentences, Group by options with the hint, the footer count, and the
// saved-view state round trip (the page is never saved). Group by is a
// compact `Group by: <value>` menu button (no select with a floating
// label), its options radio items in a menu.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import {
  agentNamesOf,
  applySavedListState,
  filtersFromUrl,
  groupOptions,
  labelOfFor,
  leftOutNotes,
  listFields,
  matchLine,
  savedListState,
  urlFromFilters,
} from "../datasetListView";
import {
  DatasetFilterChips,
  DatasetListControls,
} from "../DatasetListControls";
import { DATASET_FIELDS } from "../datasetFields";

afterEach(cleanup);

const FACETS = {
  owner: [{ value: "Asha Rao", label: "Asha Rao", count: 4 }],
  agent: [{ value: "g1", label: "Support Agent", count: 2 }],
  metadata: {
    domain: [
      { value: "billing", count: 1 },
      { value: "refunds", count: 1 },
    ],
    tier: [{ value: 2, count: 1 }],
  },
};
const AGENT = {
  column_id: "agent",
  filter_config: {
    filter_type: "categorical",
    filter_op: "in",
    filter_value: ["g1"],
  },
};

describe("datasetListView", () => {
  it("offers the answered fields and one per metadata key, typed by values", () => {
    const fields = listFields(FACETS);
    const ids = fields.map((f) => f.id);
    expect(ids).toContain("metadata.domain");
    expect(ids).not.toContain("last_pass");
    expect(ids).not.toContain("delta_vs_baseline");
    expect(fields.find((f) => f.id === "metadata.tier").type).toBe("number");
    expect(fields.find((f) => f.id === "metadata.domain").type).toBe(
      "categorical",
    );
  });

  it("names agents and kinds", () => {
    expect(agentNamesOf(FACETS)).toEqual({ g1: "Support Agent" });
    const labelOf = labelOfFor(FACETS);
    expect(labelOf({ id: "agent" }, "g1")).toBe("Support Agent");
    expect(labelOf({ id: "kind" }, "task_suite")).toBe("Task suite");
    expect(labelOf({ id: "tag" }, "golden")).toBe("golden");
  });

  it("left-out sentences per counted field", () => {
    const quality = DATASET_FIELDS.filter((f) => f.quality);
    const fields = [...listFields(FACETS), ...quality];
    expect(
      leftOutNotes({ [quality[0].id]: 2, rows: 1, tag: 0 }, fields),
    ).toEqual([
      `2 datasets have no finished run and are left out by “${quality[0].label}”.`,
      "1 dataset has no value for “Rows” and is left out.",
    ]);
  });

  it("Group by options: None + groupable fields incl. metadata keys", () => {
    const opts = groupOptions(listFields(FACETS));
    expect(opts[0]).toEqual({ id: "", label: "None" });
    expect(opts.map((o) => o.id)).toEqual([
      "",
      "kind",
      "source",
      "owner",
      "tag",
      "folder",
      "agent",
      "metadata.domain",
      "metadata.tier",
    ]);
  });

  it("footer count", () => {
    expect(matchLine(2, 9, true)).toBe("2 of 9 datasets match");
    expect(matchLine(9, 9, false)).toBe("9 datasets");
    expect(matchLine(1, 1, false)).toBe("1 dataset");
  });

  it("saved view state: never the page; applying goes to page 0", () => {
    const view = {
      q: "refund",
      sort: "name:asc",
      size: 50,
      page: 3,
      f: urlFromFilters([AGENT]),
      group: "owner",
    };
    const state = savedListState(view);
    expect(state).toEqual({
      q: "refund",
      sort: "name:asc",
      size: 50,
      f: view.f,
      group: "owner",
    });
    expect(applySavedListState(state)).toEqual({ ...state, page: 0 });
    expect(filtersFromUrl(state.f)).toEqual({ filters: [AGENT], error: null });
    expect(filtersFromUrl("{bad").filters).toEqual([]);
    expect(urlFromFilters([])).toBe("");
  });
});

describe("DatasetListControls", () => {
  it("Filter and a compact Group by menu button, no select", () => {
    const onGroup = vi.fn();
    render(
      <DatasetListControls
        filters={[]}
        onFilters={() => {}}
        group=""
        onGroup={onGroup}
        facets={FACETS}
      />,
    );
    expect(screen.getByRole("button", { name: "Filter" })).toBeTruthy();
    // One control with its value inline: no select, no floating label.
    expect(screen.queryByRole("combobox")).toBeNull();
    const group = screen.getByRole("button", { name: "Group by: None" });
    expect(group.getAttribute("aria-haspopup")).toBe("menu");
    expect(group.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(group);
    expect(group.getAttribute("aria-expanded")).toBe("true");
    const menu = screen.getByRole("menu", { name: "Group by: None" });
    expect(menu).toBeTruthy();
    const options = screen.getAllByRole("menuitemradio");
    expect(options.map((o) => o.textContent)).toEqual([
      "None",
      "Kind",
      "Source",
      "Owner",
      "Tag",
      "Folder",
      "Agent",
      "domain",
      "tier",
    ]);
    // The current value is checked and takes the keyboard focus.
    expect(
      options.filter((o) => o.getAttribute("aria-checked") === "true"),
    ).toEqual([options[0]]);
    expect(document.activeElement).toBe(options[0]);

    fireEvent.click(screen.getByRole("menuitemradio", { name: "Owner" }));
    expect(onGroup).toHaveBeenCalledWith("owner");
    expect(group.getAttribute("aria-expanded")).toBe("false");
  });

  it("Group by shows the current value; picking it again changes nothing", () => {
    const onGroup = vi.fn();
    render(
      <DatasetListControls
        filters={[]}
        onFilters={() => {}}
        group="owner"
        onGroup={onGroup}
        facets={FACETS}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Group by: Owner" }));
    const owner = screen.getByRole("menuitemradio", { name: "Owner" });
    expect(owner.getAttribute("aria-checked")).toBe("true");
    expect(
      screen
        .getByRole("menuitemradio", { name: "None" })
        .getAttribute("aria-checked"),
    ).toBe("false");
    fireEvent.click(owner);
    expect(onGroup).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Group by: Owner" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "None" }));
    expect(onGroup).toHaveBeenCalledWith("");
  });

  it("chips name the agent; removing the chip clears it", () => {
    const onFilters = vi.fn();
    render(
      <DatasetFilterChips
        filters={[AGENT]}
        onFilters={onFilters}
        facets={FACETS}
        leftOut={{}}
      />,
    );
    expect(screen.getByTestId("filter-chip").textContent).toBe(
      "Agent is Support Agent✕",
    );
    fireEvent.click(screen.getByLabelText("Remove Agent is Support Agent"));
    expect(onFilters).toHaveBeenCalledWith([]);
  });
});
