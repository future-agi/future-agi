import PropTypes from "prop-types";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  IconButton, Tooltip, Checkbox,
} from "@mui/material";

import Iconify from "src/components/iconify";
import { subTasksFor } from "src/api/simulate-environments/_fixtures/contract";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { ClampCell, SubTasksCell, TruncTooltip } from "./ScenarioTableCells";
import { SCENARIOS_COPY } from "./scenarios.constants";
import { ENV_SHAPE, GROUP_SHAPE, SCENARIO_SHAPE } from "./scenarios.shapes";

// Neutral white-on-selected checkbox — no primary colour, keeps the table's
// monochrome treatment.
const selectableCheckboxSx = {
  p: 0,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

// The per-row edit pencil opens the scenario editor through onEdit. Stripped
// from the source: the twin-seed override icon (twinSeedPrompt) — twin-only, not
// on either entry path.
export default function ScenarioTable({ rows, groups, env, onEdit, onRemove }) {
  // Two shapes come in: pre-grouped (list-view mirror) or a flat rows array.
  // Memoised so the flat fallback doesn't allocate a fresh array each render
  // (which would re-run the selection-sync effect below every render).
  const sections = useMemo(
    () => (groups?.length ? groups : [{ id: "all", label: null, rows: rows || [] }]),
    [groups, rows],
  );

  const allIds = useMemo(
    () => sections.flatMap((s) => (s.rows || []).map((r) => r.id)).filter(Boolean),
    [sections],
  );
  const [selected, setSelected] = useState(() => new Set(allIds));

  // When the ids in the table change (row added / removed), select the newly
  // visible rows and drop ones that no longer exist. `allIds` gets a new
  // identity whenever the parent rebuilds its rows array — on every poll, say —
  // so "new" has to be measured against the ids this effect last saw, not
  // against the current selection: comparing with the selection made every
  // unchecked row look new and silently re-checked it.
  const knownIdsRef = useRef(allIds);
  useEffect(() => {
    const known = knownIdsRef.current;
    const added = allIds.filter((id) => !known.includes(id));
    const removed = known.filter((id) => !allIds.includes(id));
    knownIdsRef.current = allIds;
    if (!added.length && !removed.length) return;
    setSelected((prev) => {
      const next = new Set(prev);
      added.forEach((id) => next.add(id));
      removed.forEach((id) => next.delete(id));
      return next;
    });
  }, [allIds]);

  const toggle = (id) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const toggleAll = () => setSelected((prev) => (
    prev.size === allIds.length ? new Set() : new Set(allIds)
  ));
  const allChecked = allIds.length > 0 && selected.size === allIds.length;
  const someChecked = selected.size > 0 && selected.size < allIds.length;

  const columns = ["select", "#", "Scenario", "Persona", "Situation", "Sub-goals", "Ideal outcome", ""];
  let counter = 0;

  return (
    <Box sx={{ overflowX: "auto" }}>
      <Table size="small" sx={{ minWidth: 1400 }}>
        <TableHead>
          <TableRow>
            {columns.map((h, i) => {
              const isActions = i === columns.length - 1;
              const isSelect = h === "select";
              return (
                <TableCell
                  key={h || i}
                  align={isActions ? "right" : "left"}
                  padding={isSelect ? "checkbox" : "normal"}
                  sx={{
                    typography: "s3", fontWeight: "fontWeightBold", color: "text.subtitle",
                    textTransform: "uppercase", letterSpacing: 0.4,
                    bgcolor: "background.neutral",
                    borderBottom: "1px solid", borderColor: "divider",
                    whiteSpace: "nowrap",
                    ...(h === "#" && { width: 44 }),
                    ...(isSelect && { width: 44, pl: 1.5 }),
                    ...(isActions && {
                      position: "sticky", right: 0, zIndex: 2,
                      width: 96, minWidth: 96,
                      boxShadow: (t) => `-8px 0 12px -6px ${alpha(t.palette.common.black, t.palette.mode === "dark" ? 0.45 : 0.08)}`,
                    }),
                  }}
                >
                  {isSelect ? (
                    <Checkbox
                      size="small"
                      inputProps={{ "aria-label": SCENARIOS_COPY.selectAll }}
                      checked={allChecked}
                      indeterminate={someChecked}
                      onChange={toggleAll}
                      sx={selectableCheckboxSx}
                    />
                  ) : h}
                </TableCell>
              );
            })}
          </TableRow>
        </TableHead>

        <TableBody>
          {sections.map((section) => (
            <Fragment key={section.id}>
              {section.label && (
                <TableRow>
                  <TableCell
                    colSpan={columns.length}
                    sx={{
                      position: "sticky", top: 0, zIndex: 1,
                      bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05),
                      borderTop: "1px solid", borderBottom: "1px solid", borderColor: "divider",
                      py: 1.125, px: 2,
                    }}
                  >
                    <Stack direction="row" alignItems="center" spacing={1.25}>
                      <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", color: "text.primary", flex: 1, minWidth: 0 }}>
                        {section.label}
                      </Typography>
                      <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                        {section.rows.length} {section.rows.length === 1 ? "scenario" : "scenarios"}
                      </Typography>
                    </Stack>
                  </TableCell>
                </TableRow>
              )}

              {section.rows.map((row) => {
                counter += 1;
                const idx = counter;
                const p = row.persona;
                const subTasks = row.subTasks?.length ? row.subTasks : subTasksFor(row, env);
                const personaSubline = [
                  p?.gender && p.gender.charAt(0).toUpperCase() + p.gender.slice(1),
                  p?.ageGroup,
                  !p?.gender && !p?.ageGroup && p?.role,
                ].filter(Boolean).join(" · ");
                const situationText = row.situation || row.task || "";
                const idealOutcomeText = row.outcome || row.expected || "";

                return (
                  <TableRow key={row.id} hover>
                    <TableCell padding="checkbox" sx={{ pl: 1.5, verticalAlign: "top" }}>
                      <Checkbox
                        size="small"
                        inputProps={{
                          "aria-label": SCENARIOS_COPY.selectRow(row.name || row.title || row.id),
                        }}
                        checked={selected.has(row.id)}
                        onChange={() => toggle(row.id)}
                        sx={selectableCheckboxSx}
                      />
                    </TableCell>
                    <TableCell sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums", verticalAlign: "top" }}>
                      {idx}
                    </TableCell>

                    <TableCell sx={{ maxWidth: 280, verticalAlign: "top" }}>
                      <Stack direction="row" alignItems="center" spacing={0.75}>
                        <TruncTooltip title={row.name || row.title}>
                          <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}>{row.name || row.title}</Typography>
                        </TruncTooltip>
                        {row.critical && (
                          <Tooltip arrow title={SCENARIOS_COPY.critical}>
                            <Box sx={{ display: "flex" }}>
                              <Iconify icon="solar:danger-triangle-bold" width={13} sx={{ color: BUILD_TONES.red }} />
                            </Box>
                          </Tooltip>
                        )}
                      </Stack>
                      <TruncTooltip title={row.summary || row.title}>
                        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{row.summary || row.title}</Typography>
                      </TruncTooltip>
                    </TableCell>

                    <TableCell sx={{ maxWidth: 200, verticalAlign: "top" }}>
                      <TruncTooltip title={p?.name || ""}>
                        <Typography noWrap sx={{ typography: "s2" }}>{p?.name}</Typography>
                      </TruncTooltip>
                      {personaSubline && (
                        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{personaSubline}</Typography>
                      )}
                    </TableCell>

                    <TableCell sx={{ maxWidth: 320, verticalAlign: "top" }}>
                      <ClampCell text={situationText} />
                    </TableCell>

                    <TableCell sx={{ maxWidth: 260, verticalAlign: "top" }}>
                      <SubTasksCell subTasks={subTasks} />
                    </TableCell>

                    <TableCell sx={{ maxWidth: 320, verticalAlign: "top" }}>
                      <ClampCell text={idealOutcomeText} />
                    </TableCell>

                    <TableCell
                      align="right"
                      sx={{
                        whiteSpace: "nowrap", verticalAlign: "top",
                        position: "sticky", right: 0, zIndex: 1,
                        width: 96, minWidth: 96,
                        bgcolor: "background.paper",
                        boxShadow: (t) => `-8px 0 12px -6px ${alpha(t.palette.common.black, t.palette.mode === "dark" ? 0.45 : 0.08)}`,
                        transition: "background-image 120ms ease",
                        ".MuiTableRow-hover:hover &": {
                          backgroundImage: (t) => `linear-gradient(${t.palette.action.hover}, ${t.palette.action.hover})`,
                        },
                      }}
                    >
                      <Tooltip arrow title={SCENARIOS_COPY.editLabel}>
                        <IconButton size="small" aria-label={SCENARIOS_COPY.editLabel} onClick={() => onEdit?.(row)}>
                          <Iconify icon="solar:pen-new-square-linear" width={15} sx={{ color: "text.subtitle" }} />
                        </IconButton>
                      </Tooltip>
                      <Tooltip arrow title={SCENARIOS_COPY.removeLabel}>
                        <IconButton size="small" aria-label={SCENARIOS_COPY.removeLabel} onClick={() => onRemove(row.id)}>
                          <Iconify icon="solar:trash-bin-trash-linear" width={15} sx={{ color: "text.subtitle" }} />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                );
              })}
            </Fragment>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

ScenarioTable.propTypes = {
  rows: PropTypes.arrayOf(SCENARIO_SHAPE).isRequired,
  groups: PropTypes.arrayOf(GROUP_SHAPE),
  env: ENV_SHAPE,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
};
