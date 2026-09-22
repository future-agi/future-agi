import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Table, TableBody, TableCell, TableHead, TableRow, Checkbox, Button,
} from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import MockBadge from "../../../../components/MockBadge";
import { BUILD_TONES } from "../../../../buildEnvironment/buildTones";
import {
  GROUP_SORT_ORDER, defaultTraceColumns, groupOfTask, neutralCheckboxSx,
  headCellSx, numCellSx, bodyCellSx, checkCellSx, runOutcome,
} from "./traceTable.constants";
import { MetricValue, Score, Field } from "./traceCells";
import TraceGroupHeaderRow from "./TraceGroupHeaderRow";

// Averages a numeric list, ignoring nulls.
const avgOf = (vals) => {
  const nums = vals.filter((v) => Number.isFinite(v));
  return nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : null;
};

/**
 * The per-call traces, as a grouped table. Real data only: rows read the mapped
 * `RunTask` scalars (no mock derivations); each evaluation renders as a heat-
 * tinted score column. Row-click opens the call; the checkbox column feeds the
 * bulk "Re-run N" action the parent owns.
 */
export default function TraceTable({ tasks, evals, selected, onToggle, onToggleAll, onOpen, groupBy = "useCase", columns }) {
  const [collapsed, setCollapsed] = useState(null);
  const visible = columns || defaultTraceColumns();
  const show = (key) => visible.has(key);
  const showEvals = show("evals");

  const allOn = tasks.length > 0 && tasks.every((t) => selected.has(t.id));
  const someOn = tasks.some((t) => selected.has(t.id)) && !allOn;

  const groups = useMemo(() => {
    const byKey = new Map();
    tasks.forEach((t) => {
      const label = groupOfTask(t, groupBy);
      if (label == null) return;
      if (!byKey.has(label)) byKey.set(label, []);
      byKey.get(label).push(t);
    });
    const arr = Array.from(byKey, ([label, rows]) => {
      const measured = rows.filter((r) => r.status !== "unmeasured");
      const passed = measured.filter((r) => r.status === "passed").length;
      const evalAgg = {};
      rows.forEach((r) => {
        (r.evalResults || []).forEach((er) => {
          if (!evalAgg[er.id]) evalAgg[er.id] = { scored: 0, scoreSum: 0 };
          if (er.score != null) { evalAgg[er.id].scored += 1; evalAgg[er.id].scoreSum += er.score; }
        });
      });
      const csat = avgOf(rows.map((r) => r.csat));
      const turns = avgOf(rows.map((r) => r.turns));
      const latency = avgOf(rows.map((r) => r.latencyMs));
      const tokenVals = rows.map((r) => r.tokens).filter((v) => Number.isFinite(v));
      return {
        label, rows, count: rows.length,
        measured: measured.length, passed,
        agg: {
          csat: csat != null ? Math.round(csat * 10) / 10 : null,
          turns: turns != null ? Math.round(turns * 10) / 10 : null,
          latency: latency != null ? Math.round(latency) : null,
          tokens: tokenVals.length ? tokenVals.reduce((a, b) => a + b, 0) : null,
          evals: evalAgg,
        },
      };
    });
    const order = GROUP_SORT_ORDER[groupBy];
    if (order) {
      arr.sort((x, y) => {
        const xi = order.indexOf(x.label);
        const yi = order.indexOf(y.label);
        if (xi === -1 && yi === -1) return x.label.localeCompare(y.label);
        if (xi === -1) return 1;
        if (yi === -1) return -1;
        return xi - yi;
      });
    } else {
      arr.sort((x, y) => y.count - x.count);
    }
    return arr;
  }, [tasks, groupBy]);

  const collapsedSet = collapsed ?? new Set(groups.map((g) => g.label));
  const toggleCollapsed = (label) => setCollapsed(() => {
    const next = new Set(collapsedSet);
    if (next.has(label)) next.delete(label); else next.add(label);
    return next;
  });
  const allCollapsed = groups.length > 0 && groups.every((g) => collapsedSet.has(g.label));
  const toggleAllGroups = () =>
    setCollapsed(allCollapsed ? new Set() : new Set(groups.map((g) => g.label)));

  const renderRow = (t) => {
    const outcome = runOutcome(t.status);
    return (
      <TableRow
        key={t.id}
        hover
        sx={{
          cursor: "pointer",
          "&.MuiTableRow-hover:hover": {
            bgcolor: (th) => alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.02 : 0.015),
          },
        }}
      >
        <TableCell sx={checkCellSx}>
          <Checkbox
            size="small" checked={selected.has(t.id)}
            onChange={() => onToggle(t.id)} onClick={(e) => e.stopPropagation()}
            sx={neutralCheckboxSx}
          />
        </TableCell>

        {show("callDetails") && (
          <TableCell sx={bodyCellSx} onClick={() => onOpen(t)}>
            <Box minWidth={0}>
              <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.125 }}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", maxWidth: 300 }}>
                  {t.scenario}
                </Typography>
                {t.critical && (
                  <CustomTooltip show arrow title="Critical — a failure here is a release blocker">
                    <Box sx={{ display: "flex" }}>
                      <Iconify icon="solar:danger-triangle-bold" width={12} sx={{ color: BUILD_TONES.red }} />
                    </Box>
                  </CustomTooltip>
                )}
              </Stack>
              <Stack direction="row" alignItems="center" spacing={0.75}>
                <Typography sx={{ typography: "s3", fontWeight: "fontWeightSemiBold", color: outcome.color }}>
                  {outcome.label}
                </Typography>
                {t.durationMs != null && (
                  <>
                    <Box sx={{ width: 3, height: 3, borderRadius: "50%", bgcolor: "text.disabled" }} />
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                      {(t.durationMs / 1000).toFixed(1)}s
                    </Typography>
                  </>
                )}
              </Stack>
            </Box>
          </TableCell>
        )}

        {show("persona") && (
          <TableCell sx={bodyCellSx} onClick={() => onOpen(t)}>
            {t.persona
              ? <Field icon="solar:user-id-linear" label="Name" value={t.persona} />
              : <Typography sx={{ typography: "s3", color: "text.disabled" }}>—</Typography>}
          </TableCell>
        )}

        {show("scenario") && (
          <TableCell sx={{ ...bodyCellSx, typography: "s2", color: "text.secondary" }} onClick={() => onOpen(t)}>
            {t.scenario}
          </TableCell>
        )}

        {show("csat") && <TableCell sx={numCellSx} onClick={() => onOpen(t)}><MetricValue metric="csat" value={t.csat} /></TableCell>}
        {show("turns") && <TableCell sx={numCellSx} onClick={() => onOpen(t)}><MetricValue metric="turns" value={t.turns} /></TableCell>}
        {show("latency") && <TableCell sx={numCellSx} onClick={() => onOpen(t)}><MetricValue metric="latency" value={t.latencyMs} suffix="ms" /></TableCell>}
        {show("tokens") && <TableCell sx={numCellSx} onClick={() => onOpen(t)}><MetricValue metric="tokens" value={t.tokens} /></TableCell>}

        {showEvals && evals.map((e) => {
          const r = t.evalResults?.find((x) => x.id === e.id);
          return (
            <TableCell key={e.id} sx={{ ...bodyCellSx, p: 0, position: "relative" }} onClick={() => onOpen(t)}>
              {r ? <Score result={r} /> : <Box sx={{ p: 2, typography: "s2", color: "text.disabled" }}>—</Box>}
            </TableCell>
          );
        })}
      </TableRow>
    );
  };

  return (
    <Box>
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{ px: 1.5, py: 1, borderBottom: "1px solid", borderColor: "divider" }}
      >
        <Button
          size="small" variant="text" onClick={toggleAllGroups}
          startIcon={<Iconify icon={allCollapsed ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-right-linear"} width={14} />}
          sx={{ typography: "s3", fontWeight: "fontWeightSemiBold", color: "text.secondary", "&:hover": { bgcolor: "action.hover" } }}
        >
          {allCollapsed ? "Expand all" : "Collapse all"}
        </Button>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {groups.length} {groups.length === 1 ? "group" : "groups"}
        </Typography>
      </Stack>
      <Box sx={{ overflowX: "auto" }}>
        <Table size="small" sx={{ minWidth: 1000, tableLayout: "auto" }}>
          <TableHead>
            <TableRow>
              <TableCell sx={{ ...headCellSx, ...checkCellSx }}>
                <Checkbox size="small" checked={allOn} indeterminate={someOn} onChange={onToggleAll} sx={neutralCheckboxSx} />
              </TableCell>
              {show("callDetails") && <TableCell sx={{ ...headCellSx, width: 200 }}>Run details</TableCell>}
              {show("persona") && <TableCell sx={{ ...headCellSx, width: 200 }}>Persona</TableCell>}
              {show("scenario") && <TableCell sx={{ ...headCellSx, width: 300 }}>Scenario</TableCell>}
              {show("csat") && <TableCell sx={{ ...headCellSx, width: 84 }}>CSAT</TableCell>}
              {show("turns") && <TableCell sx={{ ...headCellSx, width: 92 }}>Turns</TableCell>}
              {show("latency") && <TableCell sx={{ ...headCellSx, width: 96 }}>Latency</TableCell>}
              {show("tokens") && (
                <TableCell sx={{ ...headCellSx, width: 120 }}>
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    <span>Tokens</span>
                    <MockBadge />
                  </Stack>
                </TableCell>
              )}
              {showEvals && evals.map((e) => (
                <TableCell key={e.id} sx={{ ...headCellSx, width: 150 }}>
                  <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightMedium", color: "text.secondary" }}>{e.name}</Typography>
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {groups.map((g) => (
              <React.Fragment key={g.label}>
                <TraceGroupHeaderRow
                  group={g}
                  collapsed={collapsedSet.has(g.label)}
                  onToggle={() => toggleCollapsed(g.label)}
                  show={show}
                  showEvals={showEvals}
                  evals={evals}
                  selected={selected}
                  onToggleGroup={(rows) => {
                    const ids = rows.map((r) => r.id);
                    const everySelected = ids.every((id) => selected.has(id));
                    if (everySelected) ids.forEach((id) => onToggle(id));
                    else ids.filter((id) => !selected.has(id)).forEach((id) => onToggle(id));
                  }}
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
  tasks: PropTypes.array.isRequired,
  evals: PropTypes.array.isRequired,
  selected: PropTypes.object.isRequired,
  onToggle: PropTypes.func,
  onToggleAll: PropTypes.func,
  onOpen: PropTypes.func,
  groupBy: PropTypes.string,
  columns: PropTypes.instanceOf(Set),
};
