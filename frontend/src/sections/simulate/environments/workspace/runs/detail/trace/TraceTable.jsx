import PropTypes from "prop-types";
import React, { useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Checkbox,
  Button,
} from "@mui/material";

import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { BUILD_TONES } from "../../../../buildEnvironment/buildTones";
import {
  defaultTraceColumns,
  neutralCheckboxSx,
  headCellSx,
  numCellSx,
  bodyCellSx,
  checkCellSx,
  runOutcome,
} from "./traceTable.constants";
import { MetricValue, Score, Field } from "./traceCells";
import TraceGroupHeaderRow from "./TraceGroupHeaderRow";

/**
 * The per-call traces, as a grouped table. Real data only: rows read the mapped
 * `RunTask` scalars (no mock derivations); each evaluation renders as a heat-
 * tinted score column. Row-click opens the call; the checkbox column feeds the
 * bulk "Re-run N" action the parent owns.
 */
export default function TraceTable({
  tasks,
  groups,
  evals,
  selected,
  onToggle,
  onToggleAll,
  onOpen,
  columns,
}) {
  const [collapsed, setCollapsed] = useState(null);
  const visible = columns || defaultTraceColumns();
  const show = (key) => visible.has(key);
  const showEvals = show("evals");

  const allOn = tasks.length > 0 && tasks.every((t) => selected.has(t.id));
  const someOn = tasks.some((t) => selected.has(t.id)) && !allOn;

  const collapsedSet = collapsed ?? new Set(groups.map((g) => g.label));
  const toggleCollapsed = (label) =>
    setCollapsed(() => {
      const next = new Set(collapsedSet);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  const allCollapsed =
    groups.length > 0 && groups.every((g) => collapsedSet.has(g.label));
  const toggleAllGroups = () =>
    setCollapsed(
      allCollapsed ? new Set() : new Set(groups.map((g) => g.label)),
    );

  const renderRow = (t) => {
    const outcome = runOutcome(t.status);
    return (
      <TableRow
        key={t.id}
        sx={{
          cursor: "pointer",
          bgcolor: "transparent",
        }}
      >
        <TableCell sx={checkCellSx}>
          <Checkbox
            size="small"
            checked={selected.has(t.id)}
            onChange={() => onToggle(t.id)}
            onClick={(e) => e.stopPropagation()}
            sx={neutralCheckboxSx}
          />
        </TableCell>

        {show("callDetails") && (
          <TableCell sx={bodyCellSx} onClick={() => onOpen(t)}>
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
                    title="Critical — a failure here is a release blocker"
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

        {show("persona") && (
          <TableCell sx={bodyCellSx} onClick={() => onOpen(t)}>
            {t.personaDetails?.name ? (
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
                —
              </Typography>
            )}
          </TableCell>
        )}

        {show("scenario") && (
          <TableCell
            sx={{
              ...bodyCellSx,
              minWidth: 420,
              typography: "s2",
              color: "text.secondary",
            }}
            onClick={() => onOpen(t)}
          >
            {t.scenarioDetails || t.scenario || "—"}
          </TableCell>
        )}

        {show("idealOutcome") && (
          <TableCell
            sx={{
              ...bodyCellSx,
              minWidth: 420,
              typography: "s2",
              color: "text.secondary",
            }}
            onClick={() => onOpen(t)}
          >
            {t.idealOutcome || "—"}
          </TableCell>
        )}

        {show("conversationBranch") && (
          <TableCell
            sx={{
              ...bodyCellSx,
              minWidth: 320,
              typography: "s2",
              color: "text.secondary",
            }}
            onClick={() => onOpen(t)}
          >
            {t.conversationBranch || "—"}
          </TableCell>
        )}

        {show("csat") && (
          <TableCell sx={numCellSx} onClick={() => onOpen(t)}>
            <MetricValue metric="csat" value={t.csat} />
          </TableCell>
        )}
        {show("turns") && (
          <TableCell sx={numCellSx} onClick={() => onOpen(t)}>
            <MetricValue metric="turns" value={t.turns} />
          </TableCell>
        )}
        {show("latency") && (
          <TableCell sx={numCellSx} onClick={() => onOpen(t)}>
            <MetricValue metric="latency" value={t.latencyMs} suffix="ms" />
          </TableCell>
        )}
        {show("tokens") && (
          <TableCell sx={numCellSx} onClick={() => onOpen(t)}>
            <MetricValue metric="tokens" value={t.tokens} />
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
                {r ? (
                  <Score result={r} />
                ) : (
                  <Box sx={{ p: 2, typography: "s2", color: "text.disabled" }}>
                    —
                  </Box>
                )}
              </TableCell>
            );
          })}
      </TableRow>
    );
  };

  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
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
      <Box sx={{ overflowX: "auto" }}>
        <Table size="small" sx={{ minWidth: 1000, tableLayout: "auto" }}>
          <TableHead>
            <TableRow>
              <TableCell sx={{ ...headCellSx, ...checkCellSx }}>
                <Checkbox
                  size="small"
                  checked={allOn}
                  indeterminate={someOn}
                  onChange={onToggleAll}
                  sx={neutralCheckboxSx}
                />
              </TableCell>
              {show("callDetails") && (
                <TableCell sx={{ ...headCellSx, width: 200 }}>
                  Run details
                </TableCell>
              )}
              {show("persona") && (
                <TableCell sx={{ ...headCellSx, width: 200 }}>
                  Persona
                </TableCell>
              )}
              {show("scenario") && (
                <TableCell sx={{ ...headCellSx, width: 420, minWidth: 420 }}>
                  Scenario
                </TableCell>
              )}
              {show("idealOutcome") && (
                <TableCell sx={{ ...headCellSx, width: 420, minWidth: 420 }}>
                  Ideal outcome
                </TableCell>
              )}
              {show("conversationBranch") && (
                <TableCell sx={{ ...headCellSx, width: 320, minWidth: 320 }}>
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
                    else
                      ids
                        .filter((id) => !selected.has(id))
                        .forEach((id) => onToggle(id));
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
  groups: PropTypes.array.isRequired,
  evals: PropTypes.array.isRequired,
  selected: PropTypes.object.isRequired,
  onToggle: PropTypes.func,
  onToggleAll: PropTypes.func,
  onOpen: PropTypes.func,
  columns: PropTypes.instanceOf(Set),
};
