import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  deltaToneOf,
  fmtCellTip,
  fmtCi,
  fmtDelta,
  fmtValue,
  TONE,
} from "./workbenchFormat";
import { sortedRowKeys } from "./workbenchPivot";
import { sampleText } from "./workbenchModel";
import { deltaLine, rangeText } from "./groupNumbers";
import useScrollEdges from "src/sections/common/ux/useScrollEdges";

const tabular = { fontVariantNumeric: "tabular-nums" };
const LOW_SAMPLE_TIP = "Under 10 attempts; treat with care.";
const HEAD_W = 200;

/** `⋯` on a group header: View rows · Test a fix on this slice… */
function GroupMenu({ group, actions }) {
  const [anchor, setAnchor] = useState(null);
  if (!actions?.length) return null;
  return (
    <>
      <IconButton
        size="small"
        aria-label={`Actions for ${group}`}
        onClick={(e) => {
          e.stopPropagation();
          setAnchor(e.currentTarget);
        }}
        sx={{ p: 0.25, opacity: 0.7, "&:hover": { opacity: 1 } }}
      >
        <Iconify icon="mdi:dots-horizontal" width={16} />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { minWidth: 220 } } }}
      >
        {actions.map((a) => (
          <CustomTooltip
            key={a.label}
            show={Boolean(a.disabled)}
            title={a.disabled ?? ""}
            size="small"
            placement="left"
            arrow
          >
            <span>
              <MenuItem
                disabled={Boolean(a.disabled)}
                onClick={() => {
                  setAnchor(null);
                  a.onClick();
                }}
              >
                <ListItemIcon>
                  <Iconify icon={a.icon} width={18} />
                </ListItemIcon>
                <ListItemText primaryTypographyProps={{ typography: "s1" }}>
                  {a.label}
                </ListItemText>
              </MenuItem>
            </span>
          </CustomTooltip>
        ))}
      </Menu>
    </>
  );
}

GroupMenu.propTypes = {
  group: PropTypes.string,
  actions: PropTypes.array,
};

/**
 * The pivot under every chart (R2-SPEC §6.3.2): groups × columns, bold =
 * each column's weakest group, value + delta vs the baseline column, grey
 * `Low sample`, totals, Transpose, `Sort: most failing rows`. A cell opens
 * exactly its failing rows; arrows move between cells, Enter opens.
 */
const PivotTable = ({
  pivot,
  measure,
  groupTitle,
  colColors,
  baselineCol,
  weakest,
  sentence,
  sort,
  onSort,
  transpose,
  onTranspose,
  highlight,
  onOpenCell,
  groupActions,
  emptyText,
  deltas,
  baselineLabel,
  onOpenGroup,
}) => {
  const tableRef = useRef(null);
  const frameRef = useRef(null);
  const edges = useScrollEdges(frameRef, "[data-pivot-scroller]", [
    pivot,
    transpose,
  ]);
  const [focus, setFocus] = useState([0, 0]);
  const groups = useMemo(() => sortedRowKeys(pivot, sort), [pivot, sort]);
  const cols = pivot?.columns ?? [];
  const lines = transpose
    ? cols.map((c) => ({ key: c.key, label: c.label, col: c }))
    : groups.map((g) => ({ key: g, label: g, group: g }));
  const heads = transpose
    ? groups.map((g) => ({ key: g, label: g, group: g }))
    : cols.map((c) => ({ key: c.key, label: c.label, col: c }));
  const at = (line, head) => {
    const g = line.group ?? head.group;
    const c = line.col ?? head.col;
    return { g, c, cell: pivot.cells[g]?.[c.key] };
  };
  const lineTotal = (line) =>
    line.group ? pivot.rowTotals[line.group] : pivot.colTotals[line.col.key];
  const headTotal = (head) =>
    head.group ? pivot.rowTotals[head.group] : pivot.colTotals[head.col.key];

  const move = (e, r, c) => {
    const maxR = lines.length - 1;
    const maxC = heads.length - 1;
    let [nr, nc] = [r, c];
    if (e.key === "ArrowDown") nr = Math.min(maxR, r + 1);
    else if (e.key === "ArrowUp") nr = Math.max(0, r - 1);
    else if (e.key === "ArrowRight") nc = Math.min(maxC, c + 1);
    else if (e.key === "ArrowLeft") nc = Math.max(0, c - 1);
    else if (e.key === "Enter") {
      const { g, c: col, cell } = at(lines[r], heads[c]);
      if (cell?.rows) onOpenCell(g, col, cell);
      return;
    } else return;
    e.preventDefault();
    setFocus([nr, nc]);
    tableRef.current?.querySelector(`[data-cell="${nr}-${nc}"]`)?.focus();
  };

  // W5 B1: a group's label opens the results filtered to that group.
  const groupLinkProps = (group) =>
    group && onOpenGroup && !pivot?.failedEval
      ? {
          role: "button",
          tabIndex: 0,
          "data-testid": "wb-group-open",
          "data-group": group,
          title: `Open the results filtered to ${group}`,
          onClick: (e) => {
            e.stopPropagation();
            onOpenGroup(group);
          },
          onKeyDown: (e) => {
            if (e.key === "Enter") onOpenGroup(group);
          },
          sx: {
            cursor: "pointer",
            "&:hover": { textDecoration: "underline", color: "primary.main" },
          },
        }
      : {};

  const headCell = (item) => {
    const color = item.col ? colColors?.[item.col.key] : null;
    const isBase = item.col && item.col.key === baselineCol;
    return (
      <TableCell
        key={item.key}
        align="right"
        sx={{
          minWidth: 92,
          whiteSpace: "nowrap",
          bgcolor: "background.paper",
          borderTop: color ? `3px solid ${color.solid}` : undefined,
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="flex-end"
          gap={0.25}
        >
          {item.group && (
            <GroupMenu
              group={item.group}
              actions={groupActions?.(item.group)}
            />
          )}
          <CustomTooltip
            show={Boolean(item.col?.title)}
            title={item.col?.title ?? ""}
            size="small"
            placement="top"
            arrow
          >
            <Typography
              typography="s2"
              fontWeight="fontWeightSemiBold"
              noWrap
              {...groupLinkProps(item.group)}
            >
              {item.label}
            </Typography>
          </CustomTooltip>
        </Stack>
        {isBase && (
          <Typography typography="s3" color="text.secondary" component="div">
            Baseline
          </Typography>
        )}
        {!isBase && item.col?.sub && (
          <Typography
            typography="s3"
            color="text.secondary"
            component="div"
            noWrap
            sx={{ maxWidth: 150, ml: "auto" }}
            title={item.col.sub}
          >
            {item.col.sub}
          </Typography>
        )}
      </TableCell>
    );
  };

  if (!pivot || !groups.length || !cols.length)
    return (
      <Box sx={{ p: 3, textAlign: "center" }}>
        <Typography typography="s1" color="text.secondary">
          {emptyText ?? "No rows in this group."}
        </Typography>
      </Box>
    );

  return (
    <Box
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        gap={1}
        sx={{ px: 1.5, py: 1, borderBottom: 1, borderColor: "divider" }}
      >
        <Typography typography="s1" color="text.primary" sx={{ minWidth: 0 }}>
          {sentence ?? " "}
        </Typography>
        <Stack direction="row" alignItems="center" gap={1} flexShrink={0}>
          {!pivot.failedEval && (
            <Select
              size="small"
              value={sort}
              onChange={(e) => onSort(e.target.value)}
              renderValue={(v) =>
                `Sort: ${v === "failing" ? "most failing rows" : "default"}`
              }
              sx={{
                height: 28,
                typography: "s2",
                "& .MuiSelect-select": { py: 0.25, pl: 1 },
              }}
            >
              <MenuItem value="default" sx={{ typography: "s1" }}>
                Default order
              </MenuItem>
              <MenuItem value="failing" sx={{ typography: "s1" }}>
                Most failing rows (attempts × fail rate)
              </MenuItem>
            </Select>
          )}
          <CustomTooltip
            show
            title="Transpose"
            size="small"
            placement="top"
            arrow
          >
            <IconButton
              size="small"
              aria-label="Transpose"
              onClick={onTranspose}
              color={transpose ? "primary" : "default"}
            >
              <Iconify icon="mdi:swap-horizontal" width={18} />
            </IconButton>
          </CustomTooltip>
        </Stack>
      </Stack>
      <Box
        ref={frameRef}
        data-more-right={edges.right ? "1" : undefined}
        sx={{
          position: "relative",
          // More columns to the right: a fade on the clipped edge.
          "&::after": {
            content: '""',
            position: "absolute",
            top: 0,
            right: 0,
            bottom: 0,
            width: 40,
            pointerEvents: "none",
            zIndex: 4,
            opacity: edges.right ? 1 : 0,
            transition: "opacity 150ms",
            background: (t) =>
              `linear-gradient(to right, ${alpha(t.palette.background.paper, 0)}, ${t.palette.background.paper})`,
          },
        }}
      >
        <Box data-pivot-scroller sx={{ overflow: "auto", maxHeight: 520 }}>
          <Table
            size="small"
            stickyHeader
            ref={tableRef}
            sx={{ "& td, & th": { borderColor: "divider" } }}
          >
            <TableHead>
              <TableRow>
                <TableCell
                  sx={{
                    position: "sticky",
                    left: 0,
                    zIndex: 3,
                    bgcolor: "background.paper",
                    minWidth: HEAD_W,
                  }}
                >
                  <Typography typography="s2" color="text.secondary">
                    {transpose ? "Columns" : groupTitle}
                  </Typography>
                </TableCell>
                {heads.map((h) => headCell(h))}
                <TableCell align="right" sx={{ bgcolor: "background.paper" }}>
                  <CustomTooltip
                    show
                    size="small"
                    placement="top"
                    title={
                      transpose
                        ? "Every group together, per column."
                        : "Every run in view together, baseline included: a reference for the row, not a score for any run."
                    }
                  >
                    <Typography
                      typography="s2"
                      color="text.secondary"
                      noWrap
                      sx={{ cursor: "help" }}
                    >
                      {transpose ? "Total" : "All runs avg"}
                    </Typography>
                  </CustomTooltip>
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {lines.map((line, r) => {
                const total = lineTotal(line);
                const hl = highlight && line.group === highlight;
                return (
                  <TableRow
                    key={line.key}
                    sx={
                      hl
                        ? {
                            bgcolor: (t) => alpha(t.palette.primary.main, 0.06),
                          }
                        : undefined
                    }
                  >
                    <TableCell
                      sx={{
                        position: "sticky",
                        left: 0,
                        zIndex: 1,
                        bgcolor: hl ? "background.neutral" : "background.paper",
                        maxWidth: 260,
                      }}
                    >
                      <Stack direction="row" alignItems="center" gap={0.5}>
                        {line.col && colColors?.[line.col.key] && (
                          <Box
                            sx={{
                              width: 3,
                              height: 16,
                              borderRadius: 1,
                              bgcolor: colColors[line.col.key].solid,
                            }}
                          />
                        )}
                        <Box sx={{ flex: 1, minWidth: 0, display: "flex" }}>
                          <Typography
                            typography="s2"
                            fontWeight={500}
                            noWrap
                            title={line.label}
                            {...groupLinkProps(line.group)}
                            sx={{
                              minWidth: 0,
                              ...(groupLinkProps(line.group).sx ?? {}),
                            }}
                          >
                            {line.label}
                          </Typography>
                        </Box>
                        <Typography
                          typography="s3"
                          color="text.disabled"
                          sx={tabular}
                        >
                          {line.group ? `${total?.rows ?? 0}` : ""}
                        </Typography>
                        {line.group && (
                          <GroupMenu
                            group={line.group}
                            actions={groupActions?.(line.group)}
                          />
                        )}
                      </Stack>
                    </TableCell>
                    {heads.map((head, c) => {
                      const { g, c: col, cell } = at(line, head);
                      const isBase = col.key === baselineCol;
                      const base = baselineCol
                        ? pivot.cells[g]?.[baselineCol]
                        : null;
                      const delta =
                        !isBase &&
                        !pivot.failedEval &&
                        base?.value != null &&
                        cell?.value != null
                          ? cell.value - base.value
                          : null;
                      const paired = deltas?.[g]?.[col.key] ?? null;
                      const pairedText = paired
                        ? deltaLine(measure, paired, baselineLabel)
                        : null;
                      const tone = deltaToneOf(measure, delta);
                      const weak = weakest?.[col.key] === g;
                      const ci = fmtCi(measure, cell?.ci);
                      const tip = cell?.rows
                        ? [
                            `${col.label} · ${g}`,
                            pivot.failedEval
                              ? `${cell.n} failed attempts on ${cell.rows} rows`
                              : fmtCellTip(measure, cell),
                            ci,
                            pairedText
                              ? `${pairedText.full}`
                              : delta != null
                                ? `${fmtDelta(measure, delta, { precise: true })} vs baseline`
                                : null,
                            cell.lowSample ? LOW_SAMPLE_TIP : null,
                            pivot.failedEval || !cell.failRowIds?.length
                              ? "Click to open these rows"
                              : cell.failRowIds.length === 1
                                ? "Click to open the failing row"
                                : `Click to open the ${cell.failRowIds.length} failing rows`,
                          ]
                            .filter(Boolean)
                            .join(" · ")
                        : `${col.label} · ${g} · No rows in this group.`;
                      const focused = focus[0] === r && focus[1] === c;
                      return (
                        <TableCell
                          key={head.key}
                          align="right"
                          data-cell={`${r}-${c}`}
                          tabIndex={focused ? 0 : -1}
                          onFocus={() => setFocus([r, c])}
                          onKeyDown={(e) => move(e, r, c)}
                          onClick={() => cell?.rows && onOpenCell(g, col, cell)}
                          sx={{
                            cursor: cell?.rows ? "pointer" : "default",
                            "&:hover": cell?.rows
                              ? { bgcolor: "action.hover" }
                              : undefined,
                            "&:focus-visible": {
                              outline: (t) =>
                                `2px solid ${t.palette.primary.main}`,
                              outlineOffset: -2,
                            },
                          }}
                        >
                          <CustomTooltip
                            show
                            title={tip}
                            size="small"
                            placement="top"
                            arrow
                          >
                            <Box>
                              <Stack
                                direction="row"
                                alignItems="baseline"
                                justifyContent="flex-end"
                                gap={0.5}
                              >
                                {!paired &&
                                  delta != null &&
                                  tone !== "neutral" &&
                                  fmtDelta(measure, delta) && (
                                    <Typography
                                      typography="s3"
                                      sx={{ ...tabular, color: TONE[tone] }}
                                    >
                                      {fmtDelta(measure, delta)}
                                    </Typography>
                                  )}
                                <Typography
                                  typography="s1"
                                  sx={tabular}
                                  fontWeight={weak ? 700 : 400}
                                  color={
                                    cell?.value == null
                                      ? "text.disabled"
                                      : cell.lowSample
                                        ? "text.secondary"
                                        : "text.primary"
                                  }
                                >
                                  {pivot.failedEval
                                    ? cell?.n
                                      ? cell.n.toLocaleString("en-US")
                                      : "—"
                                    : fmtValue(measure, cell?.value)}
                                </Typography>
                              </Stack>
                              {cell?.lowSample && (
                                <Typography
                                  typography="s3"
                                  color="text.disabled"
                                  component="div"
                                  title={LOW_SAMPLE_TIP}
                                  noWrap
                                >
                                  Low sample
                                </Typography>
                              )}
                              {!pivot.failedEval && cell?.rows > 0 && (
                                <Typography
                                  typography="s3"
                                  color="text.disabled"
                                  component="div"
                                  noWrap
                                  data-testid="wb-cell-n"
                                  sx={tabular}
                                >
                                  {`n ${cell.n.toLocaleString("en-US")}${rangeText(measure, cell.ci) ? ` · ${rangeText(measure, cell.ci)}` : ""}`}
                                </Typography>
                              )}
                              {pairedText && (
                                <Typography
                                  typography="s3"
                                  component="div"
                                  noWrap
                                  data-testid="wb-cell-delta"
                                  data-significant={
                                    paired.significant ? "1" : "0"
                                  }
                                  sx={{
                                    ...tabular,
                                    color: paired.significant
                                      ? TONE[
                                          deltaToneOf(
                                            measure,
                                            paired.kind === "rate"
                                              ? paired.delta / 100
                                              : paired.delta,
                                          )
                                        ]
                                      : "text.disabled",
                                    fontWeight: paired.significant ? 600 : 400,
                                  }}
                                >
                                  {pairedText.short}
                                </Typography>
                              )}
                            </Box>
                          </CustomTooltip>
                        </TableCell>
                      );
                    })}
                    <TableCell
                      align="right"
                      sx={{ bgcolor: "background.neutral" }}
                    >
                      <Typography typography="s2" sx={tabular} fontWeight={500}>
                        {pivot.failedEval
                          ? (total?.n ?? 0).toLocaleString("en-US")
                          : fmtValue(measure, total?.value)}
                      </Typography>
                      <Typography
                        typography="s3"
                        color="text.disabled"
                        sx={{ ...tabular, whiteSpace: "nowrap" }}
                      >
                        {sampleText(total)}
                      </Typography>
                    </TableCell>
                  </TableRow>
                );
              })}
              <TableRow>
                <TableCell
                  sx={{
                    position: "sticky",
                    left: 0,
                    zIndex: 1,
                    bgcolor: "background.neutral",
                  }}
                >
                  <Typography typography="s2" fontWeight={600}>
                    Total
                  </Typography>
                </TableCell>
                {heads.map((head) => {
                  const t = headTotal(head);
                  return (
                    <TableCell
                      key={head.key}
                      align="right"
                      sx={{ bgcolor: "background.neutral" }}
                    >
                      <Typography typography="s2" sx={tabular} fontWeight={500}>
                        {pivot.failedEval
                          ? (t?.n ?? 0).toLocaleString("en-US")
                          : fmtValue(measure, t?.value)}
                      </Typography>
                      <Typography
                        typography="s3"
                        color="text.disabled"
                        sx={{ ...tabular, whiteSpace: "nowrap" }}
                      >
                        {sampleText(t)}
                      </Typography>
                    </TableCell>
                  );
                })}
                <TableCell align="right" sx={{ bgcolor: "background.neutral" }}>
                  <Typography typography="s2" sx={tabular} fontWeight={600}>
                    {pivot.failedEval
                      ? (pivot.all?.n ?? 0).toLocaleString("en-US")
                      : fmtValue(measure, pivot.all?.value)}
                  </Typography>
                  <Typography
                    typography="s3"
                    color="text.disabled"
                    sx={{ ...tabular, whiteSpace: "nowrap" }}
                  >
                    {sampleText(pivot.all)}
                  </Typography>
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </Box>
      </Box>
    </Box>
  );
};

PivotTable.propTypes = {
  pivot: PropTypes.object,
  measure: PropTypes.object,
  groupTitle: PropTypes.string,
  colColors: PropTypes.object,
  baselineCol: PropTypes.string,
  weakest: PropTypes.object,
  sentence: PropTypes.node,
  sort: PropTypes.string,
  onSort: PropTypes.func,
  transpose: PropTypes.bool,
  onTranspose: PropTypes.func,
  highlight: PropTypes.string,
  onOpenCell: PropTypes.func.isRequired,
  groupActions: PropTypes.func,
  emptyText: PropTypes.string,
  deltas: PropTypes.object,
  baselineLabel: PropTypes.string,
  onOpenGroup: PropTypes.func,
};

export default PivotTable;
