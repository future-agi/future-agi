import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import {
  addCustomColumnsToTab,
  clearSavedViewCustomColumns,
  clearSavedColumnHydrationRefs,
  createInitialTracingColumns,
  getCanonicalColumnSnapshot,
  getCustomColumnsByTab,
  mergeAuthoritativeNonCustomColumns,
  mergeColumnsWithAuthoritativeConfig,
  removeCustomColumnsFromTab,
  resetColumnsForTab,
  restoreCanonicalColumnVisibility,
  SPAN_BUILT_IN_COLUMNS,
  TRACE_BUILT_IN_COLUMNS,
} from "../defaultColumns";
import {
  mergePersistedCustomColumns,
  reorderColumns,
} from "../savedViewColumns";
import ColumnConfigureDropDown from "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown";

const expectUsableColumnList = (columns, expectedIds) => {
  expect(columns.length).toBeGreaterThan(0);
  expect(new Set(columns.map((column) => column.id)).size).toBe(columns.length);
  expectedIds.forEach((id) =>
    expect(columns.some((column) => column.id === id)).toBe(true),
  );
  columns.forEach((column) => {
    expect(column.name).toEqual(expect.any(String));
    expect(column.name.length).toBeGreaterThan(0);
    expect(typeof column.isVisible).toBe("boolean");
  });
};

describe("tracing built-in column fallbacks", () => {
  it("provides complete named trace and span lists for Display > View columns", () => {
    expectUsableColumnList(TRACE_BUILT_IN_COLUMNS, [
      "trace_name",
      "latency",
      "trace_id",
      "session_id",
    ]);
    expectUsableColumnList(SPAN_BUILT_IN_COLUMNS, [
      "span_name",
      "latency_ms",
      "span_id",
      "trace_id",
    ]);
    expect(
      TRACE_BUILT_IN_COLUMNS.find((column) => column.id === "trace_id")
        .isVisible,
    ).toBe(false);
    expect(
      SPAN_BUILT_IN_COLUMNS.find((column) => column.id === "span_id").isVisible,
    ).toBe(false);
  });

  it("renders built-in checkboxes instead of the empty-menu fallback", () => {
    const columns = createInitialTracingColumns();

    render(
      React.createElement(ColumnConfigureDropDown, {
        open: true,
        anchorEl: document.body,
        columns: columns["primary-trace"],
        onClose: () => {},
        setColumns: () => {},
        onColumnVisibilityChange: () => {},
      }),
    );

    expect(screen.getByText("Latency")).toBeInTheDocument();
    expect(screen.getByText("Trace Id")).toBeInTheDocument();
    expect(screen.queryByText("No columns found")).not.toBeInTheDocument();
  });

  it("creates independent non-empty slots for all primary/compare grids", () => {
    const columns = createInitialTracingColumns();

    expect(columns["primary-trace"]).toHaveLength(
      TRACE_BUILT_IN_COLUMNS.length,
    );
    expect(columns["primary-spans"]).toHaveLength(SPAN_BUILT_IN_COLUMNS.length);
    expect(columns["compare-trace"]).not.toBe(columns["primary-trace"]);
    expect(columns["compare-spans"]).not.toBe(columns["primary-spans"]);

    columns["primary-trace"][0].isVisible = false;
    expect(columns["compare-trace"][0].isVisible).toBe(true);
  });

  it("gives a reloaded view a base slot that retains selected custom columns", () => {
    const reloaded = createInitialTracingColumns();
    const selectedCustomColumns = [
      {
        id: "user_interruption_count",
        name: "user_interruption_count",
        isVisible: true,
        groupBy: "Custom Columns",
      },
    ];

    reloaded["primary-trace"] = mergePersistedCustomColumns(
      reloaded["primary-trace"],
      selectedCustomColumns,
    );

    expect(
      reloaded["primary-trace"].find(
        (column) => column.id === "user_interruption_count",
      ),
    ).toMatchObject(selectedCustomColumns[0]);
    expect(
      reloaded["primary-trace"].some((column) => column.id === "trace_name"),
    ).toBe(true);
  });

  it("mirrors a primary custom selection through compare autosave and reload", () => {
    const selected = {
      id: "user_interruption_count",
      name: "user_interruption_count",
      isVisible: true,
      groupBy: "Custom Columns",
    };
    let columns = addCustomColumnsToTab(
      createInitialTracingColumns(),
      "trace",
      [selected],
    );

    expect(columns["primary-trace"]).toContainEqual(selected);
    expect(columns["compare-trace"]).toContainEqual(selected);

    // selectedGraph changes do not shape persistence: serialize the union of
    // both slots, then reproduce the mount hydration into both graphs.
    const persisted = JSON.parse(
      JSON.stringify(getCustomColumnsByTab(columns)),
    );
    expect(persisted.trace).toEqual([selected]);

    const reloaded = createInitialTracingColumns();
    reloaded["primary-trace"] = mergePersistedCustomColumns(
      reloaded["primary-trace"],
      persisted.trace,
    );
    reloaded["compare-trace"] = mergePersistedCustomColumns(
      reloaded["compare-trace"],
      persisted.trace,
    );
    expect(reloaded["primary-trace"]).toContainEqual(selected);
    expect(reloaded["compare-trace"]).toContainEqual(selected);

    columns = removeCustomColumnsFromTab(columns, "trace", [selected.id]);
    expect(getCustomColumnsByTab(columns).trace).toEqual([]);
    columns = addCustomColumnsToTab(columns, "trace", [selected]);
    columns = resetColumnsForTab(columns, "trace");
    expect(getCustomColumnsByTab(columns).trace).toEqual([]);
  });

  it("resets both graph slots to canonical order and visibility", () => {
    const canonical = getCanonicalColumnSnapshot(TRACE_BUILT_IN_COLUMNS);
    const columns = createInitialTracingColumns();
    const spansBefore = columns["primary-spans"];
    ["primary-trace", "compare-trace"].forEach((slot) => {
      columns[slot] = [...columns[slot]]
        .reverse()
        .map((column) => ({ ...column, isVisible: !column.isVisible }));
      columns[slot].push({
        id: "custom.reset.me",
        name: "custom.reset.me",
        isVisible: true,
        groupBy: "Custom Columns",
      });
    });

    const reset = resetColumnsForTab(columns, "trace", canonical.columns);

    expect(reset["primary-trace"]).toEqual(canonical.columns);
    expect(reset["compare-trace"]).toEqual(canonical.columns);
    expect(reset["primary-trace"]).not.toBe(reset["compare-trace"]);
    expect(reset["primary-spans"]).toBe(spansBefore);
  });

  it("clears saved-view restamp state before resetting canonical columns", () => {
    const refs = {
      pendingColumnStateRef: { current: [{ colId: "trace_id" }] },
      pendingSavedColsRef: {
        current: [{ colId: "trace_id", hide: false }],
      },
      appliedIdSetKeyRef: { current: "trace_id|status" },
      userToggledColsRef: { current: new Set(["status"]) },
    };
    const columns = createInitialTracingColumns();
    columns["primary-trace"] = columns["primary-trace"].map((column) => ({
      ...column,
      isVisible: column.id === "trace_id",
    }));

    clearSavedColumnHydrationRefs(refs);
    const reset = resetColumnsForTab(columns, "trace", TRACE_BUILT_IN_COLUMNS);

    expect(refs.pendingColumnStateRef.current).toBeNull();
    expect(refs.pendingSavedColsRef.current).toBeNull();
    expect(refs.appliedIdSetKeyRef.current).toBeNull();
    expect(refs.userToggledColsRef.current).toEqual(new Set());
    expect(
      reset["primary-trace"].find((column) => column.id === "trace_id")
        .isVisible,
    ).toBe(false);
  });

  it("unions legacy asymmetric graph slots instead of overwriting customs", () => {
    const columns = createInitialTracingColumns();
    columns["compare-spans"].push({
      id: "legacy.compare.only",
      name: "legacy.compare.only",
      isVisible: true,
      groupBy: "Custom Columns",
    });

    expect(
      getCustomColumnsByTab(columns).spans.map((column) => column.id),
    ).toEqual(["legacy.compare.only"]);
  });

  it("promotes a custom/API id collision without duplicate voice columns", () => {
    const selected = {
      id: "user_interruption_count",
      name: "user_interruption_count",
      isVisible: true,
      groupBy: "Custom Columns",
    };
    const voiceConfig = [
      {
        id: "call_summary",
        field: "call_summary",
        name: "Call Summary",
        isVisible: true,
        groupBy: "Call Columns",
      },
      {
        id: "status",
        field: "status",
        name: "Status",
        isVisible: true,
        groupBy: "Call Columns",
      },
      {
        id: "user_interruption_count",
        field: "user_interruption_count",
        name: "User Interrupts",
        isVisible: false,
        groupBy: "Call Columns",
      },
    ];
    const canonical = getCanonicalColumnSnapshot(voiceConfig);
    let columns = createInitialTracingColumns();
    ["primary-trace", "compare-trace"].forEach((slot) => {
      columns[slot] = mergeColumnsWithAuthoritativeConfig(
        columns[slot],
        voiceConfig,
        [],
        { preserveCurrentOrder: false },
      );
    });

    // Exact UI lifecycle: the authoritative hidden field is already loaded,
    // then Add custom promotes that id in both graph slots.
    columns = addCustomColumnsToTab(columns, "trace", [selected]);
    ["primary-trace", "compare-trace"].forEach((slot) => {
      expect(columns[slot].map((column) => column.id)).toEqual(
        voiceConfig.map((column) => column.id),
      );
      const collision = columns[slot].filter(
        (column) => column.id === selected.id,
      );
      expect(collision).toHaveLength(1);
      expect(collision[0]).toMatchObject({
        field: "user_interruption_count",
        name: "User Interrupts",
        isVisible: true,
        groupBy: "Custom Columns",
      });
    });
    const persisted = JSON.parse(
      JSON.stringify(getCustomColumnsByTab(columns)),
    );
    expect(persisted.trace).toHaveLength(1);

    // Both default-tab localStorage hydration and saved-view hydration use
    // this collision-aware merge after a warm config callback.
    let reloaded = createInitialTracingColumns();
    ["primary-trace", "compare-trace"].forEach((slot) => {
      reloaded[slot] = mergeColumnsWithAuthoritativeConfig(
        reloaded[slot],
        voiceConfig,
        [],
        { preserveCurrentOrder: false },
      );
      reloaded[slot] = mergeColumnsWithAuthoritativeConfig(
        reloaded[slot],
        canonical.columns,
        persisted.trace,
      );
      expect(
        reloaded[slot].filter((column) => column.id === selected.id),
      ).toHaveLength(1);
      expect(
        reloaded[slot].find((column) => column.id === selected.id),
      ).toMatchObject({ isVisible: true, groupBy: "Custom Columns" });
    });

    const reset = resetColumnsForTab(reloaded, "trace", canonical.columns);
    expect(reset["primary-trace"]).toEqual(canonical.columns);
    expect(reset["compare-trace"]).toEqual(canonical.columns);

    reloaded = removeCustomColumnsFromTab(
      reloaded,
      "trace",
      [selected.id],
      canonical.columns,
    );
    ["primary-trace", "compare-trace"].forEach((slot) => {
      const restored = reloaded[slot].filter(
        (column) => column.id === selected.id,
      );
      expect(restored).toHaveLength(1);
      expect(restored[0]).toMatchObject({
        groupBy: "Call Columns",
        isVisible: false,
      });
    });
  });

  it("restores a promoted canonical id when the next saved view has no customs", () => {
    const voiceConfig = [
      {
        id: "call_summary",
        name: "Call Summary",
        isVisible: true,
        groupBy: "Call Columns",
      },
      {
        id: "user_interruption_count",
        name: "User Interrupts",
        isVisible: false,
        groupBy: "Call Columns",
      },
    ];
    let columns = createInitialTracingColumns();
    ["primary-trace", "compare-trace"].forEach((slot) => {
      columns[slot] = mergeColumnsWithAuthoritativeConfig(
        columns[slot],
        voiceConfig,
        [],
        { preserveCurrentOrder: false },
      );
    });
    columns = addCustomColumnsToTab(columns, "trace", [
      {
        id: "user_interruption_count",
        name: "user_interruption_count",
        isVisible: true,
        groupBy: "Custom Columns",
      },
    ]);

    // Saved view B has no customColumns. Clearing view A must restore the
    // canonical hidden field even when CallLogsGrid does not refire config.
    columns = clearSavedViewCustomColumns(
      columns,
      voiceConfig,
      SPAN_BUILT_IN_COLUMNS,
    );

    ["primary-trace", "compare-trace"].forEach((slot) => {
      const collision = columns[slot].filter(
        (column) => column.id === "user_interruption_count",
      );
      expect(collision).toHaveLength(1);
      expect(collision[0]).toMatchObject({
        name: "User Interrupts",
        isVisible: false,
        groupBy: "Call Columns",
      });
    });
  });

  it("refreshes fallback metadata from the first authoritative config", () => {
    const current = [
      { id: "status", name: "Fallback status", isVisible: false },
      { id: "trace_name", name: "Fallback name", isVisible: true },
    ];
    const authoritative = [
      {
        id: "trace_name",
        name: "Call Details",
        isVisible: false,
        groupBy: "Trace Columns",
      },
      {
        id: "status",
        name: "Status",
        isVisible: true,
        groupBy: "Trace Columns",
      },
      { id: "new_eval", name: "New eval", isVisible: false },
    ];

    const merged = mergeAuthoritativeNonCustomColumns(current, authoritative);

    // Current order and user visibility survive, while names/group metadata
    // come from the API and new API columns retain their default visibility.
    expect(merged.map((column) => column.id)).toEqual([
      "status",
      "trace_name",
      "new_eval",
    ]);
    expect(merged[0]).toMatchObject({
      name: "Status",
      groupBy: "Trace Columns",
      isVisible: false,
    });
    expect(merged[1]).toMatchObject({
      name: "Call Details",
      groupBy: "Trace Columns",
      isVisible: true,
    });
    expect(merged[2].isVisible).toBe(false);
  });

  it("restores cold voice saved views to canonical order and hidden fields", () => {
    const voiceConfig = [
      { id: "customer_name", name: "Customer", isVisible: true },
      { id: "status", name: "Status", isVisible: true },
      { id: "talk_ratio", name: "Talk Ratio", isVisible: false },
      { id: "phone_number", name: "Phone Number", isVisible: false },
      { id: "call_id", name: "Call ID", isVisible: false },
      {
        id: "response_time_ms",
        name: "Response Time",
        isVisible: false,
      },
    ];
    const canonical = getCanonicalColumnSnapshot(voiceConfig);
    const savedViewColumns = [...voiceConfig]
      .reverse()
      .map((column) => ({ ...column, isVisible: true }));

    const restored = reorderColumns(
      restoreCanonicalColumnVisibility(savedViewColumns, canonical.columns),
      canonical.order,
    );

    expect(restored.map((column) => column.id)).toEqual(canonical.order);
    ["talk_ratio", "phone_number", "call_id", "response_time_ms"].forEach(
      (id) => {
        expect(restored.find((column) => column.id === id).isVisible).toBe(
          false,
        );
      },
    );
  });
});
