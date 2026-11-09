import React, { forwardRef } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import useViewportDensity from "src/sections/common/ux/oneview/useViewportDensity";
import MatrixCell, { CELL_WIDTH, rateTint } from "./MatrixCell";
import MatrixRowHeader, { ROW_HEADER_WIDTH } from "./MatrixRowHeader";
import { HeaderLabels, MatrixColumnHeader } from "./MatrixHeader";
import { cellNumbers, pct0 } from "./matrixModel";
import { cellHeat } from "./useVoiceMatrix";
import { runFingerprint, sameSetupAs } from "./runFingerprint";
import { runShortName, runSpokenName } from "src/sections/common/Runs/runLabel";

export const cellDomId = (r, c) => `matrix-cell-${r}-${c}`;

/** IT-R3 G6 P5: the task column never grows past this. */
const LABEL_MAX_WIDTH = 420;

const stickyLeft = {
  position: "sticky",
  left: 0,
  zIndex: 2,
  bgcolor: "background.paper",
};

function GroupHeader({ group, runIds, dense, voiceGroup, onPick, runLabel }) {
  // L2 G2 (a11y #11): grid > row > rowheader / gridcell; `display:
  // contents` keeps the cells on the parent CSS grid.
  return (
    <Box role="row" sx={{ display: "contents" }}>
      <Box
        role="rowheader"
        sx={{
          ...stickyLeft,
          px: 1.25,
          height: dense ? 30 : 34,
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          bgcolor: "background.neutral",
          borderRight: "1px solid",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        {onPick ? (
          <CustomTooltip
            show
            size="small"
            title={`Show only the ${group.count} ${group.count === 1 ? "row" : "rows"} of ${group.key}`}
          >
            <Button
              size="small"
              onClick={onPick}
              sx={{
                typography: "s2",
                fontWeight: "fontWeightSemiBold",
                px: 0.5,
                minWidth: 0,
                color: "text.primary",
                height: 24,
              }}
            >
              {group.key}
            </Button>
          </CustomTooltip>
        ) : (
          <Typography typography="s2" fontWeight="fontWeightSemiBold" noWrap>
            {group.key}
          </Typography>
        )}
        <Typography typography="s3" color="text.secondary" noWrap>
          {`${group.count} ${group.count === 1 ? "row" : "rows"}`}
        </Typography>
      </Box>
      {runIds.map((id) => {
        const r = group.rates[id];
        const low = r?.lowSample;
        // R4 lane X: persona groups print calls completed (`6/8`), and ▼
        // where the run completes fewer of them than the baseline.
        const vr = voiceGroup?.perRun?.[id];
        if (vr)
          return (
            <CustomTooltip
              key={id}
              show
              size="small"
              placement="top"
              arrow
              title={`${runLabel(id)} completes ${vr.k} of ${vr.n} calls of ${group.key}${voiceGroup.worse?.[id] ? " · fewer than the baseline" : ""}`}
            >
              <Box
                role="gridcell"
                data-testid="persona-group-rate"
                sx={(t) => ({
                  height: dense ? 30 : 34,
                  px: 1,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "flex-end",
                  gap: 0.5,
                  bgcolor: rateTint(t, vr.rate),
                  borderRight: "1px solid",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                })}
              >
                <Typography
                  typography="s2"
                  fontWeight="fontWeightMedium"
                  sx={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {`${vr.k}/${vr.n}`}
                </Typography>
                {voiceGroup.worse?.[id] ? (
                  <Typography
                    typography="s2"
                    color="error.main"
                    aria-label="worse than the baseline"
                  >
                    ▼
                  </Typography>
                ) : null}
              </Box>
            </CustomTooltip>
          );
        return (
          <CustomTooltip
            key={id}
            show
            size="small"
            placement="top"
            arrow
            title={
              low
                ? `Low sample: ${r?.n ?? 0} scored ${r?.n === 1 ? "trial" : "trials"}`
                : `pass@1 ${pct0(r?.rate)} (${r?.pass}/${r?.n})`
            }
          >
            <Box
              role="gridcell"
              sx={(t) => ({
                height: dense ? 30 : 34,
                px: 1,
                display: "flex",
                alignItems: "center",
                justifyContent: "flex-end",
                gap: 0.5,
                bgcolor: low ? t.palette.action.hover : rateTint(t, r?.rate),
                borderRight: "1px solid",
                borderBottom: "1px solid",
                borderColor: "divider",
              })}
            >
              {low && (
                <Typography typography="s3" color="text.disabled" noWrap>
                  Low sample
                </Typography>
              )}
              <Typography
                typography="s2"
                fontWeight="fontWeightMedium"
                color={low ? "text.disabled" : "text.primary"}
                sx={{ fontVariantNumeric: "tabular-nums" }}
              >
                {pct0(r?.rate)}
              </Typography>
            </Box>
          </CustomTooltip>
        );
      })}
    </Box>
  );
}

GroupHeader.propTypes = {
  group: PropTypes.object,
  runIds: PropTypes.array,
  dense: PropTypes.bool,
  voiceGroup: PropTypes.object,
  onPick: PropTypes.func,
  runLabel: PropTypes.func,
};

/**
 * The Matrix grid: sticky column headers (top), sticky row headers (left,
 * 260 px) and sticky bottom rows, 132 px run columns. Pure rendering; the
 * view owns focus, hover and URL state.
 */
const MatrixGrid = forwardRef(function MatrixGrid(
  {
    model,
    runsById,
    colors,
    dense,
    showCost,
    focus,
    onKeyDown,
    onFocusGrid,
    onOpenCell,
    onHoverCell,
    onLeaveCell,
    onOpenRow,
    columnMenu,
    activeDelta,
    onDelta,
    highlighted,
    notInVersion,
    bottom,
    moreMetrics,
    onMoreMetrics,
    legend,
    onCompareRow,
    heat = "result",
    voice,
    onGroupRows,
    headerExtra,
    versionTags,
    onRemoveRun,
    roles,
    onCellMenu,
    pickedRuns,
    onPickRun,
  },
  ref,
) {
  const { runIds, columns, baselineId } = model;
  // IT-R5 G6 P3: under 820 px high the cost rows go behind More metrics.
  const costs = useViewportDensity() !== "short";
  // IT-R3 G6 P5: the task column is capped (~420 px) and the run columns
  // share the rest, so results never sit cramped on the right of an empty
  // label column (was: label 1fr, runs ≤ 180 px).
  const cols = `minmax(${ROW_HEADER_WIDTH}px, ${LABEL_MAX_WIDTH}px) repeat(${runIds.length}, minmax(${CELL_WIDTH}px, 1fr))`;
  const baselineLabel = baselineId
    ? runsById[baselineId]?.label ?? model.columnsById[baselineId]?.label
    : null;
  const flat = model.groups
    ? model.groups.flatMap((g) => [
        { group: g },
        ...g.rows.map((row) => ({ row })),
      ])
    : model.rows.map((row) => ({ row }));
  // IT-R5 G6 P3: identical headers side by side show their setup once.
  const sameAs = sameSetupAs(
    columns.map((col) => {
      const run = runsById[col.runId];
      return {
        runId: col.runId,
        label: col.label ?? run?.label,
        name: runSpokenName(run) || runShortName(run) || col.name || "",
        fingerprint: run ? runFingerprint(run).text : "",
      };
    }),
  );
  // IT-R5 G6 P3: the kind chip (`Task`) only when the rows mix kinds.
  const mixedKinds =
    new Set((model.rows ?? []).map((row) => row.kind ?? "")).size > 1;
  let rowIndex = -1;

  return (
    <Stack
      sx={{
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        // IT-R5 G6 P3: the keyboard hint shows while the grid has keyboard
        // focus (Tab into it), never on a mouse click.
        "& .mx-kbd-hint": { display: "none" },
        "&:has([role=grid]:focus-visible) .mx-kbd-hint": { display: "flex" },
      }}
    >
      <Box
        ref={ref}
        tabIndex={0}
        role="grid"
        aria-label="Rows by runs"
        aria-rowcount={model.rows.length}
        aria-colcount={runIds.length}
        aria-activedescendant={focus ? cellDomId(focus.r, focus.c) : undefined}
        onKeyDown={onKeyDown}
        onFocus={onFocusGrid}
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          position: "relative",
          outline: "none",
          "&:focus-visible": {
            boxShadow: (t) => `inset 0 0 0 1px ${t.palette.primary.main}`,
          },
        }}
      >
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: cols,
            width: "100%",
            // min-content = 260 px + 132 px per run: with many runs the grid
            // scrolls sideways only when even the minimums don't fit.
            minWidth: "min-content",
          }}
        >
          {/* ------------------------------------------------ header row */}
          {/* L2 G2 (a11y #11): the header cells sit in a role=row. */}
          <Box role="row" sx={{ display: "contents" }}>
            <Box
              role="columnheader"
              aria-label="Rows"
              sx={{
                ...stickyLeft,
                top: 0,
                zIndex: 4,
                borderRight: "1px solid",
                borderBottom: "1px solid",
                borderColor: "divider",
                bgcolor: "background.neutral",
              }}
            >
              <HeaderLabels
                k={model.trials}
                moreMetrics={moreMetrics}
                onMoreMetrics={onMoreMetrics}
                extra={headerExtra}
                costs={costs}
                legend={legend}
              />
            </Box>
            {columns.map((col) => (
              <Box
                key={col.runId}
                role="columnheader"
                sx={{
                  position: "sticky",
                  top: 0,
                  zIndex: 3,
                  bgcolor: "background.neutral",
                  borderRight: "1px solid",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <MatrixColumnHeader
                  run={
                    runsById[col.runId] ?? {
                      run_id: col.runId,
                      label: col.label,
                      name: col.name,
                    }
                  }
                  stats={col}
                  color={colors[col.runId]}
                  isBaseline={col.runId === baselineId}
                  baselineLabel={baselineLabel}
                  menu={columnMenu(col.runId)}
                  activeDelta={
                    activeDelta?.runId === col.runId ? activeDelta.dir : null
                  }
                  onDelta={(dir) => onDelta(col.runId, dir)}
                  highlighted={highlighted === col.runId}
                  moreMetrics={moreMetrics}
                  costs={costs}
                  versionTag={versionTags?.[col.runId]}
                  roleText={roles?.[col.runId]}
                  sameAs={sameAs[col.runId] ?? undefined}
                  picked={Boolean(pickedRuns?.includes(col.runId))}
                  onPick={onPickRun ? () => onPickRun(col.runId) : undefined}
                  onRemove={
                    onRemoveRun ? () => onRemoveRun(col.runId) : undefined
                  }
                  removeReason={
                    onRemoveRun && col.runId === baselineId
                      ? "Make another run the baseline first"
                      : onRemoveRun && runIds.length <= 2
                        ? "A comparison needs two runs"
                        : null
                  }
                  voice={
                    voice
                      ? {
                          regressions: voice.regressions?.[col.runId] ?? [],
                          textOnly: voice.textOnly?.has(col.runId),
                        }
                      : undefined
                  }
                />
              </Box>
            ))}
          </Box>

          {/* --------------------------------------------------- body */}
          {flat.map((item) => {
            if (item.group)
              return (
                <GroupHeader
                  key={`g:${item.group.key}`}
                  group={item.group}
                  runIds={runIds}
                  dense={dense}
                  voiceGroup={voice?.groups?.[item.group.key]}
                  runLabel={(id) =>
                    runsById[id]?.label ?? model.columnsById[id]?.label ?? ""
                  }
                  onPick={
                    onGroupRows ? () => onGroupRows(item.group) : undefined
                  }
                />
              );
            const { row } = item;
            rowIndex += 1;
            const r = rowIndex;
            const rowMaxCost = Math.max(
              0,
              ...runIds.map((id) => cellNumbers(row.cells?.[id]).cost ?? 0),
            );
            // `data-oneview-row`: a data row (one view audit + row tooling);
            // `display: contents` keeps the cells on the parent grid.
            return (
              <Box
                key={row.row_id}
                role="row"
                data-oneview-row=""
                sx={{ display: "contents" }}
              >
                {/* The row header carries the marker too: a `display:
                    contents` box has no rect (the audit's first-row y). */}
                <Box sx={stickyLeft} data-oneview-row="">
                  <MatrixRowHeader
                    row={row}
                    dense={dense}
                    focused={focus?.r === r && focus?.c === -1}
                    showKind={mixedKinds}
                    onOpen={() => onOpenRow(row)}
                    onCompare={
                      onCompareRow ? () => onCompareRow(row) : undefined
                    }
                  />
                </Box>
                {runIds.map((id, c) => {
                  const call = voice?.calls?.[row.row_id]?.[id] ?? null;
                  return (
                    <MatrixCell
                      key={id}
                      cellId={cellDomId(r, c)}
                      cell={row.cells?.[id] ?? null}
                      call={call}
                      heat={heat}
                      heatValue={cellHeat(heat, {
                        call,
                        cost: cellNumbers(row.cells?.[id]).cost,
                        rowMaxCost,
                      })}
                      runLabel={
                        runsById[id]?.label ??
                        model.columnsById[id]?.label ??
                        ""
                      }
                      rowLabel={row.label}
                      runKind={
                        model.columnsById[id]?.kind ?? runsById[id]?.kind
                      }
                      rowMaxCost={rowMaxCost}
                      showCost={showCost}
                      dense={dense}
                      focused={focus?.r === r && focus?.c === c}
                      notInVersion={notInVersion(id)}
                      onOpen={(e) => onOpenCell(row, id, { r, c }, e)}
                      onMenu={
                        onCellMenu ? (e) => onCellMenu(e, row, id) : undefined
                      }
                      onHover={(e) => onHoverCell(e.currentTarget, row, id)}
                      onLeave={onLeaveCell}
                    />
                  );
                })}
              </Box>
            );
          })}
        </Box>
      </Box>
      <Box
        className="mx-kbd-hint"
        data-testid="matrix-keyboard-hint"
        aria-hidden
        sx={{
          flexShrink: 0,
          gap: 1.5,
          px: 1,
          py: 0.25,
          typography: "s3",
          color: "text.secondary",
          bgcolor: "background.neutral",
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <span>↑ ↓ ← → move between cells</span>
        <span>Enter opens the cell</span>
        <span>Esc closes</span>
      </Box>
      {/* Bottom lines under the grid (not over it): rows end above them,
        clear of the Falcon bubble at the bottom right. */}
      <Box
        sx={{
          flexShrink: 0,
          bgcolor: "background.paper",
          borderTop: "1px solid",
          borderColor: "divider",
          overflowX: "auto",
        }}
      >
        {bottom}
      </Box>
    </Stack>
  );
});

MatrixGrid.propTypes = {
  model: PropTypes.object.isRequired,
  runsById: PropTypes.object.isRequired,
  colors: PropTypes.object.isRequired,
  dense: PropTypes.bool,
  showCost: PropTypes.bool,
  focus: PropTypes.object,
  onKeyDown: PropTypes.func,
  onFocusGrid: PropTypes.func,
  onOpenCell: PropTypes.func,
  onHoverCell: PropTypes.func,
  onLeaveCell: PropTypes.func,
  onOpenRow: PropTypes.func,
  columnMenu: PropTypes.func,
  activeDelta: PropTypes.object,
  onDelta: PropTypes.func,
  highlighted: PropTypes.string,
  notInVersion: PropTypes.func,
  bottom: PropTypes.node,
  moreMetrics: PropTypes.bool,
  legend: PropTypes.node,
  onMoreMetrics: PropTypes.func,
  onCompareRow: PropTypes.func,
  // R4 lane X
  heat: PropTypes.string,
  voice: PropTypes.object,
  onGroupRows: PropTypes.func,
  headerExtra: PropTypes.node,
  versionTags: PropTypes.object,
  // IT-R4 G6 P1 (rule 30): `{[runId]: "Baseline · current" | "New" | …}`.
  roles: PropTypes.object,
  // IT-R4 G6 P7: `(event, row, runId)` opens the cell menu.
  onCellMenu: PropTypes.func,
  onRemoveRun: PropTypes.func,
  // IT-R5 G6 P3: run ids ticked in the headers (at most 2).
  pickedRuns: PropTypes.arrayOf(PropTypes.string),
  onPickRun: PropTypes.func,
};

/** One line of the bottom block: label + text + optional action. */
export function BottomLine({ label, children, action }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{ px: 1.25, minHeight: 32, py: 0.25 }}
    >
      <Typography
        typography="s2"
        fontWeight="fontWeightMedium"
        sx={{ width: ROW_HEADER_WIDTH - 20, flexShrink: 0 }}
        noWrap
      >
        {label}
      </Typography>
      <Typography
        typography="s2"
        color="text.secondary"
        sx={{ fontVariantNumeric: "tabular-nums" }}
      >
        {children}
      </Typography>
      {action}
    </Stack>
  );
}

BottomLine.propTypes = {
  label: PropTypes.node,
  children: PropTypes.node,
  action: PropTypes.node,
};

export function CalloutAction({ children, onClick, tooltip }) {
  const btn = (
    <Button
      size="small"
      onClick={onClick}
      sx={{ typography: "s2", whiteSpace: "nowrap" }}
    >
      {children}
    </Button>
  );
  return tooltip ? (
    <CustomTooltip show size="small" placement="top" arrow title={tooltip}>
      {btn}
    </CustomTooltip>
  ) : (
    btn
  );
}

CalloutAction.propTypes = {
  children: PropTypes.node,
  onClick: PropTypes.func,
  tooltip: PropTypes.string,
};

export default MatrixGrid;
