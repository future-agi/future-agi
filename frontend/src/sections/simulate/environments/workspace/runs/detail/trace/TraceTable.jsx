import PropTypes from "prop-types";
import React, { useEffect, useRef, useState } from "react";
import {
  Box,
  Checkbox,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
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

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import SoonChip from "../../../../components/SoonChip";
import {
  defaultTraceColumns,
  headCellSx,
  bandCellSx,
  GROUP_BAND_PX,
  TRACE_COLUMNS,
  HEAD_ROW_PX,
  GROUP_ROW_PX,
  numCellSx,
  bodyCellSx,
  runOutcome,
  CLOSED_GROUP_VIEW,
  neutralCheckboxSx,
  pinnedLeftSx,
} from "./traceTable.constants";
import {
  SubTasksCell,
  TruncTooltip,
} from "../../../scenarios/ScenarioTableCells";
import {
  CsatValue,
  MetricValue,
  Score,
  Field,
  UnscoredEval,
} from "./traceCells";
import TraceGroupHeaderRow from "./TraceGroupHeaderRow";
import SubGoalsSummary from "./SubGoalsSummary";

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

/**
 * The per-call traces, as a grouped table. Real data only: rows read the mapped
 * `RunTask` scalars (no mock derivations); each evaluation renders as a heat-
 * tinted score column. Row-click opens the call; past results are read-only.
 */
// The free-text columns stay narrow so a collapsed table (one count per group)
// doesn't stretch; long text is cut at four lines — the drawer has the rest.
const TEXT_COL_WIDTH = { long: 260, short: 200 };
// A call still in flight — its eval cells can only be waiting. `analyzing`
// is a finished conversation whose evals are grading (the chat path).
const LIVE_CALL_STATUSES = new Set([
  "pending",
  "queued",
  "ongoing",
  "analyzing",
]);
const textCellSx = (width) => ({
  ...bodyCellSx,
  width,
  minWidth: width,
  maxWidth: width,
  typography: "s2",
  color: "text.secondary",
});
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
const clampSx = {
  display: "-webkit-box",
  WebkitLineClamp: 4,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
  wordBreak: "break-word",
};

// A persona is worth showing when any field is filled, not only the name: the
// API sends name: null when the persona has no name key.
const hasPersonaDetails = (p) =>
  !!(p && (p.name || p.voice || p.age || p.traits?.length));

// A row is one keyboard stop; this ring shows where focus is.
const rowFocusSx = {
  "&:focus-visible": {
    outline: "2px solid",
    outlineColor: "primary.main",
    outlineOffset: -2,
  },
};

// The persona, as one line to scan by: who, then how old and how they sound.
// Every field, traits included, is on the hover card.
function PersonaCell({ persona }) {
  if (!hasPersonaDetails(persona))
    return (
      <Typography sx={{ typography: "s3", color: "text.disabled" }}>
        -
      </Typography>
    );
  const sub = [persona.age, persona.voice].filter(Boolean).join(" · ");
  return (
    <CustomTooltip
      show
      arrow
      size="small"
      placement="right"
      title={
        <Stack spacing={0.5} sx={{ maxWidth: 300 }}>
          <Field
            icon="solar:user-id-linear"
            label="Name"
            value={persona.name}
          />
          <Field icon="solar:user-linear" label="Voice" value={persona.voice} />
          <Field
            icon="solar:users-group-rounded-linear"
            label="Age"
            value={persona.age}
          />
          <Field
            icon="solar:tag-linear"
            label="Traits"
            value={persona.traits?.join(", ")}
          />
        </Stack>
      }
    >
      <Box sx={{ minWidth: 0, maxWidth: 210 }}>
        <Typography
          noWrap
          sx={{ typography: "s2", fontWeight: "fontWeightMedium" }}
        >
          {persona.name || "-"}
        </Typography>
        {sub && (
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            {sub}
          </Typography>
        )}
      </Box>
    </CustomTooltip>
  );
}
PersonaCell.propTypes = { persona: PropTypes.object };

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
  trials = 1,
  trialsSplit = false,
}) {
  // The row whose ⋯ menu is open: { task, anchorEl } or null.
  const [rowMenu, setRowMenu] = useState(null);
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
    const outcome = runOutcome(t.status);
    const callLive = LIVE_CALL_STATUSES.has(t.executionStatus);
    const active = t.id === activeCallId;
    return (
      <TableRow
        key={t.id}
        ref={active ? activeRowRef : undefined}
        aria-selected={active}
        // Every cell opens the call by mouse; by keyboard the row is one stop
        // and Enter opens it. Only the row's own Enter: a control inside it
        // (the tick, the menu) keeps its key.
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.target === e.currentTarget && e.key === "Enter") {
            e.preventDefault();
            onOpen(t);
          }
        }}
        sx={{
          cursor: "pointer",
          bgcolor: active ? "action.selected" : "transparent",
          // Scrolled into view below the pinned head and group rows, not under.
          scrollMarginTop: GROUP_BAND_PX + HEAD_ROW_PX + GROUP_ROW_PX,
          ...rowFocusSx,
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
        {show("callDetails") && (
          // Indented past the group row's chevron so a call reads as nested
          // under its group, lined up with the group name.
          <TableCell sx={{ ...bodyCellSx, pl: 5 }} onClick={() => onOpen(t)}>
            <Box minWidth={0}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={0.75}
                sx={{ mb: 0.125 }}
              >
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    fontWeight: "fontWeightSemiBold",
                    maxWidth: 300,
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {t.scenario}
                </Typography>
                <IconButton
                  size="small"
                  aria-label="Row actions"
                  aria-haspopup="menu"
                  onClick={(e) => {
                    e.stopPropagation();
                    setRowMenu({ task: t, anchorEl: e.currentTarget });
                  }}
                  onKeyDown={(e) => e.stopPropagation()}
                  sx={{ p: 0.25, flexShrink: 0 }}
                >
                  <Iconify
                    icon="solar:menu-dots-bold"
                    width={14}
                    sx={{ color: "text.subtitle" }}
                  />
                </IconButton>
              </Stack>
              <Stack direction="row" alignItems="center" spacing={0.75}>
                <Typography
                  sx={{
                    typography: "s3",
                    fontWeight: "fontWeightSemiBold",
                    color: outcome.color,
                  }}
                >
                  {outcome.label}
                </Typography>
                {t.durationMs != null && (
                  <>
                    <Box
                      sx={{
                        width: 3,
                        height: 3,
                        borderRadius: "50%",
                        bgcolor: "text.disabled",
                      }}
                    />
                    <Typography
                      sx={{ typography: "s3", color: "text.subtitle" }}
                    >
                      {(t.durationMs / 1000).toFixed(1)}s
                    </Typography>
                  </>
                )}
              </Stack>
              <SubGoalsSummary results={t.subGoalResults} />
            </Box>
          </TableCell>
        )}

        {show("persona") && (
          <TableCell sx={bodyCellSx} onClick={() => onOpen(t)}>
            <PersonaCell persona={t.personaDetails} />
          </TableCell>
        )}

        {show("scenario") && (
          <TableCell
            sx={textCellSx(TEXT_COL_WIDTH.long)}
            onClick={() => onOpen(t)}
          >
            <Box sx={clampSx}>{t.scenario || "-"}</Box>
          </TableCell>
        )}

        {show("situation") && (
          <TableCell
            sx={textCellSx(TEXT_COL_WIDTH.long)}
            onClick={() => onOpen(t)}
          >
            <TruncTooltip title={t.scenarioDetails}>
              <Box sx={clampSx}>{t.scenarioDetails || "-"}</Box>
            </TruncTooltip>
          </TableCell>
        )}

        {show("subGoals") && (
          <TableCell
            sx={textCellSx(TEXT_COL_WIDTH.long)}
            onClick={() => onOpen(t)}
          >
            <SubTasksCell
              subTasks={(t.subGoalResults || []).map((goal) => goal.name)}
            />
          </TableCell>
        )}

        {show("idealOutcome") && (
          <TableCell
            sx={textCellSx(TEXT_COL_WIDTH.long)}
            onClick={() => onOpen(t)}
          >
            <TruncTooltip title={t.idealOutcome}>
              <Box sx={clampSx}>{t.idealOutcome || "-"}</Box>
            </TruncTooltip>
          </TableCell>
        )}

        {show("conversationBranch") && (
          <TableCell
            sx={textCellSx(TEXT_COL_WIDTH.short)}
            onClick={() => onOpen(t)}
          >
            <Box sx={clampSx}>{t.conversationBranch || "-"}</Box>
          </TableCell>
        )}

        {show("csat") && (
          <TableCell sx={numCellSx} onClick={() => onOpen(t)}>
            <CsatValue
              value={t.csat}
              status={t.csatStatus}
              reason={t.csatReason}
              callLive={callLive}
            />
          </TableCell>
        )}
        {show("turns") && (
          <TableCell sx={numCellSx} onClick={() => onOpen(t)}>
            <MetricValue metric="turns" value={t.turns} loading={callLive} />
          </TableCell>
        )}
        {show("latency") && (
          <TableCell sx={numCellSx} onClick={() => onOpen(t)}>
            <MetricValue
              metric="latency"
              value={t.latencyMs}
              suffix="ms"
              loading={callLive}
              missingTip="No response time was reported."
            />
          </TableCell>
        )}
        {show("stopLatency") && (
          <TableCell sx={numCellSx} onClick={() => onOpen(t)}>
            <MetricValue
              metric="stopLatency"
              value={t.stopLatencyMs}
              suffix="ms"
              loading={callLive}
            />
          </TableCell>
        )}
        {show("aiInterruptions") && (
          <TableCell sx={numCellSx} onClick={() => onOpen(t)}>
            <MetricValue
              metric="aiInterruptions"
              value={t.aiInterruptions}
              loading={callLive}
            />
          </TableCell>
        )}
        {show("tokens") && (
          <TableCell sx={numCellSx} onClick={() => onOpen(t)}>
            <MetricValue metric="tokens" value={t.tokens} loading={callLive} />
          </TableCell>
        )}

        {scoredColumns.map((e) => {
          const r = t.evalResults?.find((x) => x.id === e.id);
          return (
            <TableCell
              key={e.id}
              sx={{ ...bodyCellSx, p: 0, position: "relative" }}
              // A verdict opens the call on that eval; an empty cell opens
              // the call.
              onClick={() => (r ? onOpen(t, { evalId: e.id }) : onOpen(t))}
            >
              {r?.score != null || r?.label ? (
                <Score result={r} />
              ) : (
                <UnscoredEval
                  result={r}
                  callLive={callLive}
                  callStatus={t.executionStatus}
                />
              )}
            </TableCell>
          );
        })}
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
              {show("callDetails") && (
                <TableCell sx={{ ...headSx, width: 200 }}>
                  {firstColumnLabel}
                </TableCell>
              )}
              {show("persona") && (
                <TableCell sx={{ ...headSx, width: 200 }}>Persona</TableCell>
              )}
              {show("scenario") && (
                <TableCell sx={{ ...headSx, width: TEXT_COL_WIDTH.long }}>
                  Scenario
                </TableCell>
              )}
              {show("situation") && (
                <TableCell sx={{ ...headSx, width: TEXT_COL_WIDTH.long }}>
                  Situation
                </TableCell>
              )}
              {show("subGoals") && (
                <TableCell sx={{ ...headSx, width: TEXT_COL_WIDTH.long }}>
                  Sub-goals
                </TableCell>
              )}
              {show("idealOutcome") && (
                <TableCell sx={{ ...headSx, width: TEXT_COL_WIDTH.long }}>
                  Ideal outcome
                </TableCell>
              )}
              {show("conversationBranch") && (
                <TableCell sx={{ ...headSx, width: TEXT_COL_WIDTH.short }}>
                  Conversation branch
                </TableCell>
              )}
              {show("csat") && (
                <TableCell sx={{ ...headSx, width: 84 }}>CSAT</TableCell>
              )}
              {show("turns") && (
                <TableCell sx={{ ...headSx, width: 92 }}>Turns</TableCell>
              )}
              {show("latency") && (
                <TableCell sx={{ ...headSx, width: 96 }}>Latency</TableCell>
              )}
              {show("stopLatency") && (
                <TableCell sx={{ ...headSx, width: 140 }}>
                  Stop latency
                </TableCell>
              )}
              {show("aiInterruptions") && (
                <TableCell sx={{ ...headSx, width: 150 }}>
                  AI interruptions
                </TableCell>
              )}
              {show("tokens") && (
                <TableCell sx={{ ...headSx, width: 120 }}>Tokens</TableCell>
              )}
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
                      trials={trials}
                      trialsSplit={trialsSplit}
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
      <Menu
        anchorEl={rowMenu?.anchorEl}
        open={!!rowMenu}
        onClose={() => setRowMenu(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 200 } } }}
      >
        <MenuItem
          onClick={() => {
            const task = rowMenu?.task;
            setRowMenu(null);
            if (task) onOpen(task);
          }}
          sx={{ typography: "s2" }}
        >
          <ListItemIcon>
            <Iconify icon="solar:eye-linear" width={16} />
          </ListItemIcon>
          <ListItemText
            primary="Open call"
            primaryTypographyProps={{ typography: "s2" }}
          />
        </MenuItem>
        {/* Whether the scenario must pass isn't sent with a call yet. */}
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ px: 2, py: 0.75 }}
        >
          <Typography
            sx={{ typography: "s2", color: "text.disabled", flex: 1 }}
          >
            Must-pass
          </Typography>
          <SoonChip
            label="Must-pass"
            what="Whether a scenario must pass"
            size="tiny"
          />
        </Stack>
      </Menu>
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
  // Opens a call: `onOpen(task)`, or `onOpen(task, { evalId })` from an eval
  // cell, to land on that eval.
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
  // The run's repeats per scenario, so a group can count its scenarios.
  trials: PropTypes.number,
  // The axis can split one scenario's repeats across groups (Status), so a
  // group counts calls instead.
  trialsSplit: PropTypes.bool,
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
