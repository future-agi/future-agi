import PropTypes from "prop-types";
import React, { useEffect, useRef, useState } from "react";
import {
  Box,
  Checkbox,
  Stack,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  tableBodyClasses,
  tableCellClasses,
  tableHeadClasses,
  tableRowClasses,
} from "@mui/material";

import {
  defaultTraceColumns,
  headCellSx,
  bandCellSx,
  GROUP_BAND_PX,
  TRACE_COLUMNS,
  HEAD_ROW_PX,
  GROUP_ROW_PX,
  bodyCellSx,
  CLOSED_GROUP_VIEW,
  neutralCheckboxSx,
  pinnedLeftSx,
  LIVE_CALL_STATUSES,
  TRACE_CELL_KEYS,
  TRACE_HEAD_WIDTH,
} from "./traceTable.constants";
import { EvalCell, TraceCell } from "./traceRowCells";
import TraceGroupHeaderRow from "./TraceGroupHeaderRow";

// The theme hides every border on a table's last row, which here is the head
// row and the final call row. The column dividers are cell left borders, so put
// those back, the head's bottom line, and the body's last bottom line, which
// closes the table when its rows don't fill the scroll box. Separate borders,
// because collapsed ones stay behind when the head and group rows stick.
const lastRowDividersSx = {
  minWidth: 1000,
  tableLayout: "auto",
  borderCollapse: "separate",
  borderSpacing: 0,
  [`& .${tableRowClasses.root}:last-of-type .${tableCellClasses.root}:not(:first-of-type)`]:
    { borderLeftColor: "divider" },
  [`& .${tableHeadClasses.root} .${tableCellClasses.root}, & .${tableBodyClasses.root} .${tableRowClasses.root}:last-of-type .${tableCellClasses.root}`]:
    { borderBottomColor: "divider" },
};

// The narrow first column that holds a call's tick when rows can be selected.
const checkboxCellSx = {
  ...bodyCellSx,
  ...pinnedLeftSx,
  zIndex: 1,
  width: 44,
  minWidth: 44,
  px: 1,
  textAlign: "center",
};
// The head row centres its labels, so its box is centred too, level with
// "Use case" rather than at the top like the call rows' boxes.
const checkboxHeadSx = {
  width: 44,
  minWidth: 44,
  px: 1,
  textAlign: "center",
  verticalAlign: "middle",
};
/**
 * The per-call traces, as a grouped table. Real data only: rows read the mapped
 * `RunTask` scalars (no mock derivations); each evaluation renders as a heat-
 * tinted score column. Row-click opens the call; past results are read-only.
 */
export default function TraceTable({
  groups,
  rows = null,
  evals,
  subGoalEvals = [],
  firstColumnLabel = "Run details",
  onOpen,
  columns,
  activeCallId = null,
  scrollRef,
  groupView: groupViewProp,
  onGroupViewChange,
  expandedForRef: expandedForRefProp,
  runActive = false,
  renderEvalHeaderAction = null,
  selection = null,
}) {
  // A parent that unmounts this table (a filter's loading or empty state)
  // passes the open/closed groups in, so they survive the remount. Without
  // one, the table keeps them itself.
  const [ownGroupView, setOwnGroupView] = useState(CLOSED_GROUP_VIEW);
  const groupView = onGroupViewChange
    ? groupViewProp ?? CLOSED_GROUP_VIEW
    : ownGroupView;
  const setGroupView = onGroupViewChange || setOwnGroupView;
  const activeRowRef = useRef(null);
  // The call already expanded for, so a group the user collapses afterwards
  // stays collapsed across refetches.
  const ownExpandedForRef = useRef(null);
  const expandedForRef = expandedForRefProp || ownExpandedForRef;
  const visible = columns || defaultTraceColumns();
  const show = (key) => visible.has(key);
  const showSubGoalEvals = show("subGoalEvals");
  const scoredColumns = [...(showSubGoalEvals ? subGoalEvals : []), ...evals];
  // The band above the head row: one segment per run of columns sharing a
  // group, in column order, then the scored columns under their own names.
  const bandSegments = TRACE_COLUMNS.filter(
    (c) => c.key !== "subGoalEvals" && show(c.key),
  ).reduce((acc, c) => {
    const last = acc[acc.length - 1];
    if (last && last.name === c.group) last.span += 1;
    else acc.push({ name: c.group, span: 1 });
    return acc;
  }, []);
  if (showSubGoalEvals && subGoalEvals.length)
    bandSegments.push({ name: "Sub-goal Results", span: subGoalEvals.length });
  if (evals.length)
    bandSegments.push({ name: "Evaluations", span: evals.length });
  const headSx = { ...headCellSx, top: GROUP_BAND_PX };
  // The server's group figures can't tell a call still running from one with no
  // value. So a group counts as still coming while one of its calls here is
  // live, or, while the run goes on, while some of its calls are on other pages.
  const groupLive = (g) =>
    g.rows.some((t) => LIVE_CALL_STATUSES.has(t.executionStatus)) ||
    (runActive && g.rows.length < g.count);

  const isOpen = (label) => groupView.all || groupView.expanded.has(label);
  // Closing a group ends Expand all, but the groups it opened stay open.
  const toggleGroup = (label) =>
    setGroupView((prev) => {
      const expanded = new Set(prev.expanded);
      if (prev.all || expanded.has(label)) {
        if (prev.all) groups.forEach((g) => expanded.add(g.label));
        expanded.delete(label);
        return { all: false, expanded };
      }
      expanded.add(label);
      return { ...prev, expanded };
    });
  // Under Expand all, groups that turn up from another filter or page open
  // too. Record them, so closing one later leaves these open.
  useEffect(() => {
    if (!groupView.all || groups.every((g) => groupView.expanded.has(g.label)))
      return;
    setGroupView((prev) =>
      prev.all
        ? {
            ...prev,
            expanded: new Set([
              ...prev.expanded,
              ...groups.map((g) => g.label),
            ]),
          }
        : prev,
    );
  }, [groupView, groups, setGroupView]);
  // The open call's row must be visible: expand its group once per call, then
  // bring the row into view.
  const activeGroupLabel = activeCallId
    ? groups.find((g) => g.rows.some((row) => row.id === activeCallId))?.label
    : undefined;
  useEffect(() => {
    if (!activeGroupLabel || expandedForRef.current === activeCallId) return;
    expandedForRef.current = activeCallId;
    setGroupView((prev) =>
      prev.all || prev.expanded.has(activeGroupLabel)
        ? prev
        : { ...prev, expanded: new Set([...prev.expanded, activeGroupLabel]) },
    );
  }, [activeCallId, activeGroupLabel, expandedForRef, setGroupView]);
  // Its row only mounts once its group expands, so scroll again when that
  // happens — not just when the call changes.
  const activeGroupCollapsed = !isOpen(activeGroupLabel);
  useEffect(() => {
    activeRowRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [activeCallId, activeGroupLabel, activeGroupCollapsed]);

  const renderRow = (t) => {
    const active = t.id === activeCallId;
    return (
      <TableRow
        key={t.id}
        ref={active ? activeRowRef : undefined}
        aria-selected={active}
        sx={{
          cursor: "pointer",
          bgcolor: active ? "action.selected" : "transparent",
          // Scrolled into view below the pinned head and group rows, not under.
          scrollMarginTop: GROUP_BAND_PX + HEAD_ROW_PX + GROUP_ROW_PX,
        }}
      >
        {selection && (
          // The tick is its own control: this cell has no open-the-call click,
          // and a row click elsewhere opens the call without touching it.
          <TableCell
            sx={{
              ...checkboxCellSx,
              // The row's open-call tint, layered over the opaque base.
              ...(active && {
                backgroundImage: (th) =>
                  `linear-gradient(${th.palette.action.selected}, ${th.palette.action.selected})`,
              }),
            }}
          >
            {selection.canSelect(t) && (
              <Checkbox
                size="small"
                checked={selection.isSelected(t.id)}
                onChange={() => selection.onToggle(t)}
                inputProps={{ "aria-label": `Select ${t.scenario}` }}
                sx={{ p: 0.25, ...neutralCheckboxSx }}
              />
            )}
          </TableCell>
        )}
        {TRACE_CELL_KEYS.filter(show).map((key) => (
          <TraceCell key={key} columnKey={key} task={t} onOpen={onOpen} />
        ))}
        {scoredColumns.map((e) => (
          <EvalCell key={e.id} column={e} task={t} onOpen={onOpen} />
        ))}
      </TableRow>
    );
  };

  // The table scrolls both ways in its own box, so the head and group rows
  // stick to it rather than to the page.
  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <Box ref={scrollRef} sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <Table size="small" sx={lastRowDividersSx}>
          <TableHead>
            {bandSegments.length > 0 && (
              <TableRow>
                {selection && (
                  <TableCell
                    sx={{ ...bandCellSx, ...pinnedLeftSx, zIndex: 4 }}
                  />
                )}
                {bandSegments.map((segment) => (
                  <TableCell
                    key={segment.name}
                    colSpan={segment.span}
                    sx={bandCellSx}
                  >
                    {segment.name}
                  </TableCell>
                ))}
              </TableRow>
            )}
            <TableRow>
              {selection && (
                <TableCell
                  sx={{
                    ...headSx,
                    ...checkboxHeadSx,
                    ...pinnedLeftSx,
                    zIndex: 4,
                  }}
                >
                  <Checkbox
                    size="small"
                    checked={selection.pageChecked}
                    indeterminate={selection.pageIndeterminate}
                    disabled={!selection.pageSelectable}
                    onChange={(e) => selection.onTogglePage(e.target.checked)}
                    inputProps={{
                      "aria-label": "Select all calls on this page",
                    }}
                    sx={{ p: 0.25, ...neutralCheckboxSx }}
                  />
                </TableCell>
              )}
              {TRACE_CELL_KEYS.filter(show).map((key) => (
                <TableCell
                  key={key}
                  sx={{ ...headSx, width: TRACE_HEAD_WIDTH[key] }}
                >
                  {key === "callDetails"
                    ? firstColumnLabel
                    : TRACE_COLUMNS.find((c) => c.key === key).label}
                </TableCell>
              ))}
              {scoredColumns.map((e) => {
                // Sub-goal columns share this row but aren't evals, so only
                // an eval's own column gets the action.
                const action =
                  renderEvalHeaderAction && evals.includes(e)
                    ? renderEvalHeaderAction(e)
                    : null;
                const name = (
                  <Typography
                    noWrap
                    sx={{
                      typography: "s2",
                      fontWeight: "fontWeightMedium",
                      color: "text.secondary",
                      ...(action && { flex: 1, minWidth: 0 }),
                    }}
                  >
                    {e.name}
                  </Typography>
                );
                return (
                  <TableCell key={e.id} sx={{ ...headSx, width: 150 }}>
                    {action ? (
                      <Stack direction="row" alignItems="center" spacing={0.5}>
                        {name}
                        <Box sx={{ flexShrink: 0 }}>{action}</Box>
                      </Stack>
                    ) : (
                      name
                    )}
                  </TableCell>
                );
              })}
            </TableRow>
          </TableHead>
          <TableBody>
            {rows
              ? rows.map(renderRow)
              : groups.map((g) => (
                  <React.Fragment key={g.label}>
                    <TraceGroupHeaderRow
                      group={g}
                      loading={groupLive(g)}
                      collapsed={!isOpen(g.label)}
                      onToggle={() => toggleGroup(g.label)}
                      top={GROUP_BAND_PX + HEAD_ROW_PX}
                      show={show}
                      showEvals={scoredColumns.length > 0}
                      evals={scoredColumns}
                      leadingCell={!!selection}
                      checkbox={
                        selection && {
                          ...selection.groupState(g),
                          onChange: () => selection.onToggleGroup(g),
                        }
                      }
                    />
                    {isOpen(g.label) && g.rows.map(renderRow)}
                  </React.Fragment>
                ))}
          </TableBody>
        </Table>
      </Box>
    </Box>
  );
}
TraceTable.propTypes = {
  groups: PropTypes.array.isRequired,
  rows: PropTypes.array,
  evals: PropTypes.array.isRequired,
  subGoalEvals: PropTypes.array,
  // The head of the first column: the axis the rows are grouped by.
  firstColumnLabel: PropTypes.string,
  onOpen: PropTypes.func,
  columns: PropTypes.instanceOf(Set),
  activeCallId: PropTypes.string,
  scrollRef: PropTypes.oneOfType([PropTypes.func, PropTypes.object]),
  // Which groups are open: `all` while Expand all is on, plus the labels
  // opened. Pass both, or neither and the table keeps its own.
  groupView: PropTypes.shape({
    all: PropTypes.bool,
    expanded: PropTypes.instanceOf(Set),
  }),
  onGroupViewChange: PropTypes.func,
  expandedForRef: PropTypes.shape({ current: PropTypes.any }),
  runActive: PropTypes.bool,
  // Optional per-eval node shown after the eval's name in its header cell.
  renderEvalHeaderAction: PropTypes.func,
  // Present when calls can be ticked: adds a checkbox column. `canSelect`
  // decides per call; the page box covers this page's selectable calls.
  selection: PropTypes.shape({
    isSelected: PropTypes.func.isRequired,
    canSelect: PropTypes.func.isRequired,
    onToggle: PropTypes.func.isRequired,
    onTogglePage: PropTypes.func.isRequired,
    groupState: PropTypes.func.isRequired,
    onToggleGroup: PropTypes.func.isRequired,
    pageChecked: PropTypes.bool,
    pageIndeterminate: PropTypes.bool,
    pageSelectable: PropTypes.bool,
  }),
};
