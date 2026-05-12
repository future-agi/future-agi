import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useUrlState } from "src/routes/hooks/use-url-state";

import TraceGrid from "../../LLMTracing/TraceGrid";
import ObserveToolbar from "../../LLMTracing/ObserveToolbar";
import CustomColumnDialog from "../../LLMTracing/CustomColumnDialog";
import FilterChips from "../../LLMTracing/FilterChips";
import ColumnConfigureDropDown from "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown";
import { transformDateFilterToBackendFilters } from "../common";

// New trace view embedded inside UserDetails. Mounts LLMTracing's TraceGrid
// pre-filtered to the current user, with the full ObserveToolbar (Filter +
// Display + Custom Columns) the user gets on the main trace list page.
const UserTraceTabV2 = ({ dateFilter }) => {
  const { observeId, userId } = useParams();
  const [selectedProjectId] = useUrlState("projectId", null);
  const projectId = observeId || selectedProjectId;

  const [_loading, setLoading] = useState(false);
  const [columns, setColumns] = useState([]);
  const [extraFilters, setExtraFilters] = useState([]);
  const [cellHeight, setCellHeight] = useUrlState(
    "userTraceCellHeight",
    "Short",
  );
  const [isFilterOpen, setIsFilterOpen] = useUrlState(
    "userTraceFilterOpen",
    false,
  );
  const [openCustomColumn, setOpenCustomColumn] = useState(false);
  const [columnConfigureAnchor, setColumnConfigureAnchor] = useState(null);
  const openColumnConfigure = Boolean(columnConfigureAnchor);
  const pendingCustomColumnsRef = useRef([]);

  // Persist custom columns to localStorage so they survive a refresh on
  // this user-detail trace tab. Scoped per {project, user} to match the
  // URL — same-user across projects gets its own set since available
  // attributes (the source for custom cols) is project-specific.
  const customColsStorageKey = useMemo(
    () => `user-trace-customcols-${projectId}-${userId}`,
    [projectId, userId],
  );
  // Two-phase guard. The hydrate writes into a ref, not state, so on the
  // first render `columns` is still empty when the save effect fires in
  // the same flush — without this gate it would call removeItem and wipe
  // the saved customs before TraceGrid's merge has a chance to drain the
  // pending ref into columns state.
  //
  // - `hasDrainedRef` flips true on the first render that observes a
  //   non-empty `columns` array (i.e. after TraceGrid's merge landed).
  //   Until then the save effect refuses to delete the stored customs.
  // - `skipNextSaveRef` (mirrors the Sessions / UsersView pattern) skips
  //   the very next save fire after a hydrate so the in-batch closure
  //   over pre-hydrate state can't overwrite what we just loaded.
  const hasDrainedRef = useRef(false);
  const skipNextSaveRef = useRef(false);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(customColsStorageKey);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (Array.isArray(saved) && saved.length > 0) {
        // Shallow-clone each col so the pending ref (and `columns` it
        // drains into) doesn't share object identity with the parsed
        // localStorage payload. Cheap defensive isolation matching the
        // LLMTracingView / Sessions-view pattern.
        pendingCustomColumnsRef.current = saved.map((c) => ({ ...c }));
        skipNextSaveRef.current = true;
      }
    } catch {
      /* ignore corrupted localStorage */
    }
  }, [customColsStorageKey]);

  useEffect(() => {
    if (skipNextSaveRef.current) {
      skipNextSaveRef.current = false;
      return;
    }
    const customCols = (columns || []).filter(
      (c) => c.groupBy === "Custom Columns",
    );
    // Don't overwrite stored customs until at least one merge has landed,
    // otherwise the first-render empty `columns` would delete the saved
    // entry before TraceGrid drains the pending ref.
    if (!hasDrainedRef.current && customCols.length === 0) {
      if ((columns || []).length > 0) {
        // Real merge with no customs — fine to persist the empty state.
        hasDrainedRef.current = true;
      } else {
        return;
      }
    } else {
      hasDrainedRef.current = true;
    }
    try {
      if (customCols.length > 0) {
        localStorage.setItem(customColsStorageKey, JSON.stringify(customCols));
      } else {
        localStorage.removeItem(customColsStorageKey);
      }
    } catch {
      /* quota exceeded */
    }
  }, [columns, customColsStorageKey]);

  // Build validated filter list: user_id + date range + any user-added extras.
  const validatedFilters = useMemo(() => {
    const base = [
      {
        columnId: "user_id",
        filterConfig: {
          filterOp: "equals",
          filterType: "text",
          filterValue: userId,
        },
      },
      ...(transformDateFilterToBackendFilters(dateFilter) || []),
    ];
    return base;
  }, [userId, dateFilter]);

  const { data: evalAttributes } = useQuery({
    queryKey: ["eval-attributes", projectId],
    queryFn: () =>
      axios.get(endpoints.project.getEvalAttributeList(), {
        params: {
          filters: JSON.stringify({ project_id: projectId }),
        },
      }),
    select: (data) => data.data?.result,
    enabled: Boolean(projectId),
  });
  const attributes = useMemo(() => evalAttributes || [], [evalAttributes]);

  const handleAddCustomColumns = (newCols) => {
    setColumns((prev) => {
      const existingIds = new Set((prev || []).map((c) => c.id));
      const deduped = newCols.filter((c) => !existingIds.has(c.id));
      return [...(prev || []), ...deduped];
    });
  };

  const handleRemoveCustomColumns = (idsToRemove) => {
    const removeSet = new Set(idsToRemove || []);
    setColumns((prev) =>
      (prev || []).filter(
        (c) => !(c.groupBy === "Custom Columns" && removeSet.has(c.id)),
      ),
    );
  };

  // Build a lookup so each chip carries a human-readable `display_name`.
  // Without this, UUID-based trace filters render as the ambiguous
  // "Column <8-char-id>" fallback in FilterChips.
  const columnLabelLookup = useMemo(() => {
    const m = {};
    for (const c of columns || []) {
      const id = c?.id;
      if (!id) continue;
      m[id] = c?.name || c?.headerName || c?.label || id;
    }
    return m;
  }, [columns]);

  return (
    <Box sx={{ px: 1.5 }}>
      <ObserveToolbar
        mode="traces"
        // Filter
        hasActiveFilter={extraFilters.length > 0}
        isFilterOpen={isFilterOpen}
        onFilterToggle={() => setIsFilterOpen(!isFilterOpen)}
        onApplyExtraFilters={setExtraFilters}
        // Columns / Display
        columns={columns}
        onColumnVisibilityChange={(e) =>
          setColumnConfigureAnchor(e?.currentTarget || null)
        }
        setColumns={setColumns}
        onAddCustomColumn={() => setOpenCustomColumn(true)}
        // Row height
        cellHeight={cellHeight}
        setCellHeight={setCellHeight}
        // Row count for status bar display
        rowCount={undefined}
      />

      <FilterChips
        extraFilters={/** @type {any[]} */ (extraFilters).map((f) => ({
          ...f,
          display_name:
            columnLabelLookup[f?.column_id] ?? f?.display_name,
        }))}
        onRemoveFilter={(idx) =>
          setExtraFilters((prev) => prev.filter((_, i) => i !== idx))
        }
        onClearAll={() => setExtraFilters([])}
      />

      <TraceGrid
        columns={columns}
        setColumns={setColumns}
        filters={validatedFilters}
        extraFilters={extraFilters}
        setFilters={setExtraFilters}
        setFilterOpen={setIsFilterOpen}
        setLoading={setLoading}
        projectId={projectId}
        cellHeight={cellHeight}
        pendingCustomColumnsRef={pendingCustomColumnsRef}
      />

      <ColumnConfigureDropDown
        open={openColumnConfigure}
        onClose={() => setColumnConfigureAnchor(null)}
        anchorEl={columnConfigureAnchor}
        columns={columns}
        setColumns={setColumns}
        onColumnVisibilityChange={(updatedData) =>
          setColumns((cols) =>
            (cols || []).map((c) => ({
              ...c,
              isVisible: updatedData[c.id] ?? c.isVisible,
            })),
          )
        }
        useGrouping
      />

      <CustomColumnDialog
        open={openCustomColumn}
        onClose={() => setOpenCustomColumn(false)}
        attributes={attributes}
        existingColumns={columns}
        onAddColumns={handleAddCustomColumns}
        onRemoveColumns={handleRemoveCustomColumns}
      />
    </Box>
  );
};

UserTraceTabV2.propTypes = {
  dateFilter: PropTypes.object,
};

export default UserTraceTabV2;
