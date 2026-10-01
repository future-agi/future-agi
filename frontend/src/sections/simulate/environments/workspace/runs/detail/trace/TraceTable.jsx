import PropTypes from "prop-types";
import React, { useEffect, useRef, useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Button,
  tableBodyClasses,
  tableCellClasses,
  tableHeadClasses,
  tableRowClasses,
} from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { BUILD_TONES } from "../../../../buildEnvironment/buildTones";
import {
  defaultTraceColumns,
  headCellSx,
  HEAD_ROW_PX,
  GROUP_ROW_PX,
  numCellSx,
  bodyCellSx,
  runOutcome,
  CALL_STATUS_CHIPS,
} from "./traceTable.constants";
import StatusChip from "../../StatusChip";
import { MetricValue, Score, Field, UnscoredEval } from "./traceCells";
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
const LIVE_CALL_STATUSES = new Set(["pending", "queued", "ongoing", "analyzing"]);
const textCellSx = (width) => ({
  ...bodyCellSx,
  width,
  minWidth: width,
  maxWidth: width,
  typography: "s2",
  color: "text.secondary",
});
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

export default function TraceTable({
  groups,
  rows = null,
  evals,
  onOpen,
  columns,
  activeCallId = null,
  scrollRef,
  runActive = false,
}) {
  const [collapsed, setCollapsed] = useState(null);
  const activeRowRef = useRef(null);
  // The call already expanded for, so a group the user collapses afterwards
  // stays collapsed across refetches.
  const expandedForRef = useRef(null);
  const visible = columns || defaultTraceColumns();
  const show = (key) => visible.has(key);
  const showEvals = show("evals");
  // The server's group figures can't tell a call still running from one with no
  // value. So a group counts as still coming while one of its calls here is
  // live, or, while the run goes on, while some of its calls are on other pages.
  const groupLive = (g) =>
    g.rows.some((t) => LIVE_CALL_STATUSES.has(t.executionStatus)) ||
    (runActive && g.rows.length < g.count);

  const collapsedSet = collapsed ?? new Set(groups.map((g) => g.label));
  const toggleCollapsed = (label) =>
    setCollapsed(() => {
      const next = new Set(collapsedSet);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  // The open call's row must be visible: expand its group once per call, then
  // bring the row into view.
  const activeGroupLabel = activeCallId
    ? groups.find((g) => g.rows.some((row) => row.id === activeCallId))?.label
    : undefined;
  useEffect(() => {
    if (!activeGroupLabel || expandedForRef.current === activeCallId) return;
    expandedForRef.current = activeCallId;
    setCollapsed((prev) => {
      const next = new Set(prev ?? groups.map((g) => g.label));
      next.delete(activeGroupLabel);
      return next;
    });
  }, [activeCallId, activeGroupLabel, groups]);
  // Its row only mounts once its group expands, so scroll again when that
  // happens — not just when the call changes.
  const activeGroupCollapsed = collapsedSet.has(activeGroupLabel);
  useEffect(() => {
    activeRowRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [activeCallId, activeGroupLabel, activeGroupCollapsed]);

  const allCollapsed =
    groups.length > 0 && groups.every((g) => collapsedSet.has(g.label));
  const toggleAllGroups = () =>
    setCollapsed(
      allCollapsed ? new Set() : new Set(groups.map((g) => g.label)),
    );

  const renderRow = (t) => {
    const outcome = runOutcome(t.status);
    const callLive = LIVE_CALL_STATUSES.has(t.executionStatus);
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
          scrollMarginTop: HEAD_ROW_PX + GROUP_ROW_PX,
        }}
      >
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
                  }}
                >
                  {t.scenario}
                </Typography>
                {t.critical && (
                  <CustomTooltip
                    show
                    arrow
                    title="Critical: a failure here is a release blocker"
                  >
                    <Box sx={{ display: "flex" }}>
                      <Iconify
                        icon="solar:danger-triangle-bold"
                        width={12}
                        sx={{ color: BUILD_TONES.red }}
                      />
                    </Box>
                  </CustomTooltip>
                )}
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
            </Box>
          </TableCell>
        )}

        {show("status") && (
          <TableCell sx={bodyCellSx} onClick={() => onOpen(t)}>
            {CALL_STATUS_CHIPS[t.executionStatus] ? (
              <Box sx={{ display: "inline-flex" }}>
                <StatusChip
                  status={CALL_STATUS_CHIPS[t.executionStatus].chip}
                  label={CALL_STATUS_CHIPS[t.executionStatus].label}
                />
              </Box>
            ) : (
              <Typography sx={{ typography: "s3", color: "text.disabled" }}>
                -
              </Typography>
            )}
          </TableCell>
        )}

        {show("persona") && (
          <TableCell sx={bodyCellSx} onClick={() => onOpen(t)}>
            {hasPersonaDetails(t.personaDetails) ? (
              <Stack spacing={0.5} sx={{ minWidth: 210 }}>
                <Field
                  icon="solar:user-id-linear"
                  label="Name"
                  value={t.personaDetails.name}
                />
                <Field
                  icon="solar:user-linear"
                  label="Voice"
                  value={t.personaDetails.voice}
                />
                <Field
                  icon="solar:users-group-rounded-linear"
                  label="Age"
                  value={t.personaDetails.age}
                />
                <Field
                  icon="solar:tag-linear"
                  label="Traits"
                  value={t.personaDetails.traits?.join(", ")}
                />
              </Stack>
            ) : (
              <Typography sx={{ typography: "s3", color: "text.disabled" }}>
                -
              </Typography>
            )}
          </TableCell>
        )}

        {show("scenario") && (
          <TableCell
            sx={textCellSx(TEXT_COL_WIDTH.long)}
            onClick={() => onOpen(t)}
          >
            <Box sx={clampSx}>{t.scenarioDetails || t.scenario || "-"}</Box>
          </TableCell>
        )}

        {show("idealOutcome") && (
          <TableCell
            sx={textCellSx(TEXT_COL_WIDTH.long)}
            onClick={() => onOpen(t)}
          >
            <Box sx={clampSx}>{t.idealOutcome || "-"}</Box>
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
            <MetricValue metric="csat" value={t.csat} loading={callLive} />
          </TableCell>
        )}
        {show("turns") && (
          <TableCell sx={numCellSx} onClick={() => onOpen(t)}>
            <MetricValue metric="turns" value={t.turns} loading={callLive} />
          </TableCell>
        )}
        {show("latency") && (
          <TableCell sx={numCellSx} onClick={() => onOpen(t)}>
            <MetricValue metric="latency" value={t.latencyMs} suffix="ms" loading={callLive} />
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

        {showEvals &&
          evals.map((e) => {
            const r = t.evalResults?.find((x) => x.id === e.id);
            return (
              <TableCell
                key={e.id}
                sx={{ ...bodyCellSx, p: 0, position: "relative" }}
                onClick={() => onOpen(t)}
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

  // The table scrolls both ways in its own box, below the "Collapse all" bar,
  // so the head and group rows stick to it rather than to the page.
  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {!rows && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            flexShrink: 0,
            px: 1.5,
            py: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Button
            size="small"
            variant="text"
            onClick={toggleAllGroups}
            startIcon={
              <Iconify
                icon={
                  allCollapsed
                    ? "solar:alt-arrow-down-linear"
                    : "solar:alt-arrow-right-linear"
                }
                width={14}
              />
            }
            sx={{
              typography: "s3",
              fontWeight: "fontWeightSemiBold",
              color: "text.secondary",
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            {allCollapsed ? "Expand all" : "Collapse all"}
          </Button>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {groups.length} {groups.length === 1 ? "group" : "groups"}
          </Typography>
        </Stack>
      )}
      <Box ref={scrollRef} sx={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        <Table size="small" sx={lastRowDividersSx}>
          <TableHead>
            <TableRow>
              {show("callDetails") && (
                <TableCell sx={{ ...headCellSx, width: 200 }}>
                  Run details
                </TableCell>
              )}
              {show("status") && (
                <TableCell sx={{ ...headCellSx, width: 120 }}>
                  Status
                </TableCell>
              )}
              {show("persona") && (
                <TableCell sx={{ ...headCellSx, width: 200 }}>
                  Persona
                </TableCell>
              )}
              {show("scenario") && (
                <TableCell sx={{ ...headCellSx, width: TEXT_COL_WIDTH.long }}>
                  Scenario
                </TableCell>
              )}
              {show("idealOutcome") && (
                <TableCell sx={{ ...headCellSx, width: TEXT_COL_WIDTH.long }}>
                  Ideal outcome
                </TableCell>
              )}
              {show("conversationBranch") && (
                <TableCell sx={{ ...headCellSx, width: TEXT_COL_WIDTH.short }}>
                  Conversation branch
                </TableCell>
              )}
              {show("csat") && (
                <TableCell sx={{ ...headCellSx, width: 84 }}>CSAT</TableCell>
              )}
              {show("turns") && (
                <TableCell sx={{ ...headCellSx, width: 92 }}>Turns</TableCell>
              )}
              {show("latency") && (
                <TableCell sx={{ ...headCellSx, width: 96 }}>Latency</TableCell>
              )}
              {show("stopLatency") && (
                <TableCell sx={{ ...headCellSx, width: 140 }}>
                  Stop latency
                </TableCell>
              )}
              {show("aiInterruptions") && (
                <TableCell sx={{ ...headCellSx, width: 150 }}>
                  AI interruptions
                </TableCell>
              )}
              {show("tokens") && (
                <TableCell sx={{ ...headCellSx, width: 120 }}>Tokens</TableCell>
              )}
              {showEvals &&
                evals.map((e) => (
                  <TableCell key={e.id} sx={{ ...headCellSx, width: 150 }}>
                    <Typography
                      noWrap
                      sx={{
                        typography: "s2",
                        fontWeight: "fontWeightMedium",
                        color: "text.secondary",
                      }}
                    >
                      {e.name}
                    </Typography>
                  </TableCell>
                ))}
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
                      collapsed={collapsedSet.has(g.label)}
                      onToggle={() => toggleCollapsed(g.label)}
                      show={show}
                      showEvals={showEvals}
                      evals={evals}
                    />
                    {!collapsedSet.has(g.label) && g.rows.map(renderRow)}
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
  onOpen: PropTypes.func,
  columns: PropTypes.instanceOf(Set),
  activeCallId: PropTypes.string,
  scrollRef: PropTypes.oneOfType([PropTypes.func, PropTypes.object]),
  runActive: PropTypes.bool,
};
