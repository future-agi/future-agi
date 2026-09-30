import PropTypes from "prop-types";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  IconButton, Tooltip, Checkbox,
} from "@mui/material";

import Iconify from "src/components/iconify";
import DraggableColResizer from "src/components/draggable-col-resizer";
import { getStorage, setStorage } from "src/hooks/use-local-storage";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import { ClampCell, SubTasksCell, TruncTooltip } from "./ScenarioTableCells";
import { ROW_HEIGHTS, SCENARIOS_COPY } from "./scenarios.constants";
import { GROUP_SHAPE, SCENARIO_SHAPE } from "./scenarios.shapes";

// A seeded-from-template env is read-only until forked; every mutating control
// on the table carries this on a tooltip so the disabled state reads as
// intentional rather than broken.
const LOCK_TOOLTIP = "Fork this environment to edit.";

// Height of the sticky column-header row (size="small" + s3 type ≈ 28.5px).
// The group headers pin at this offset so they rest just under it, not on top.
const HEAD_OFFSET = 28;

const WIDTHS_KEY = "simulate:scenario-table:column-widths";
const DEFAULT_WIDTHS = { Scenario: 280, Persona: 200, Situation: 320, "Sub-goals": 260, "Ideal outcome": 320 };
const FIXED_WIDTHS = { select: 48, "#": 44, "": 96 };
const FILLER = "filler";
const edgeLine = (color) => ({ backgroundColor: "transparent", "&::after": { bgcolor: color } });
const RESIZE_HANDLE_SX = {
  position: "absolute", top: 0, right: 0, bottom: 0,
  width: 6, height: "auto", mx: 0, borderRadius: 0,
  "&::after": {
    content: '""', position: "absolute", top: 0, bottom: 0, right: 0, width: "1px",
    bgcolor: "transparent", transition: "background-color 120ms",
  },
  "&:hover": edgeLine("border.hover"),
  "&:active": edgeLine("border.active"),
};

function readWidths() {
  const saved = getStorage(WIDTHS_KEY) || {};
  return Object.fromEntries(
    Object.entries(DEFAULT_WIDTHS).map(([key, width]) => [key, Number(saved[key]) > 0 ? Number(saved[key]) : width]),
  );
}

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
export default function ScenarioTable({ rows, groups, onEdit, onRemove, onHideGroup, selectedIds, onSelectionChange, selection, pageIds, onTogglePage, locked = false, rowHeight = "Short" }) {
  const limits = ROW_HEIGHTS[rowHeight] || ROW_HEIGHTS.Short;
  const [widths, setWidths] = useState(readWidths);
  const widthsRef = useRef(widths);
  widthsRef.current = widths;
  const resizeHandlers = useMemo(
    () => Object.fromEntries(
      Object.keys(DEFAULT_WIDTHS).map((key) => [key, {
        onResize: (next) => setWidths((prev) => ({ ...prev, [key]: next })),
        getCurrentWidth: () => widthsRef.current[key],
      }]),
    ),
    [],
  );
  const saveWidths = useCallback(() => setStorage(WIDTHS_KEY, widthsRef.current), []);

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

  // Selection is a bulk-action affordance, not a run gate — every scenario in
  // the table runs on the next simulation regardless of checked state. So the
  // table opens with nothing selected; the checkboxes only light up when the
  // user wants to Delete or bulk-edit a group of rows together.
  //
  // Controlled from the parent (ScenariosStep) so the SelectionBar and the
  // builder chat stay in sync; falls back to internal state when unwired.
  const [internalSelected, setInternalSelected] = useState(() => new Set());
  const isControlled = Array.isArray(selectedIds);
  const selected = isControlled ? new Set(selectedIds) : internalSelected;

  const commit = (next) => {
    if (!isControlled) setInternalSelected(next);
    onSelectionChange?.(Array.from(next));
  };

  // The predicate-based selection model (useSelection) survives paging, so a
  // page of rows is the *view*, not the selection. When it's supplied, drive
  // the checkboxes off it and skip the legacy full-list bookkeeping below —
  // most importantly the id-cleanup effect, which under paging would drop every
  // selection that isn't on the current page.
  const model = selection || null;

  // Legacy path only: drop selection ids for rows that no longer exist.
  useEffect(() => {
    if (model) return;
    const cleaned = Array.from(selected).filter((id) => allIds.includes(id));
    if (cleaned.length !== selected.size) commit(new Set(cleaned));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allIds.join("|")]);

  const toggle = (id) => {
    if (model) { model.toggle(id); return; }
    const next = new Set(selected);
    if (next.has(id)) next.delete(id); else next.add(id);
    commit(next);
  };
  const rowChecked = (id) => (model ? model.isSelected(id) : selected.has(id));

  // Header checkbox is scoped to the visible page. In model mode its tri-state
  // comes from the page's ids and toggling escalates through the parent (which
  // knows the whole-match count); in legacy mode it selects/clears the flat list.
  const headerState = model ? model.pageState(pageIds || []) : null;
  const allChecked = model ? headerState.allChecked : allIds.length > 0 && selected.size === allIds.length;
  const someChecked = model ? headerState.someChecked : selected.size > 0 && selected.size < allIds.length;
  const toggleAll = () => {
    if (model) { onTogglePage?.(!allChecked); return; }
    const next = selected.size === allIds.length ? new Set() : new Set(allIds);
    commit(next);
  };

  // A locked template hides bulk-select entirely — the checkbox column and the
  // per-row checkboxes drop out, so there's no way to start a bulk action the
  // fork gate would only refuse.
  const columns = [
    ...(locked ? [] : ["select"]),
    "#", "Scenario", "Persona", "Situation", "Sub-goals", "Ideal outcome", FILLER, "",
  ];
  const widthOf = (h) => widths[h] ?? FIXED_WIDTHS[h] ?? 0;
  const tableWidth = columns.reduce((sum, h) => sum + widthOf(h), 0);

  return (
    // No own scroll wrapper: the parent (PagedScenarioViews) owns the scroll
    // container for BOTH axes. A scroll box here would become the sticky
    // containing block and the column/group headers would pin to it — a box
    // that doesn't scroll vertically — instead of the parent's bounded box.
    <Table
      size="small"
      // Separate borders (spacing 0) so the sticky <thead> cells actually pin —
      // position:sticky on table-header cells is broken under the default
      // border-collapse:collapse. Spacing 0 keeps the collapsed look.
      sx={{ tableLayout: "fixed", width: "100%", minWidth: tableWidth, borderCollapse: "separate", borderSpacing: 0 }}
    >
        <TableHead>
          <TableRow sx={{ "&:last-of-type .MuiTableCell-root": { borderColor: "divider" } }}>
            {columns.map((h, i) => {
              const isActions = i === columns.length - 1;
              const isSelect = h === "select";
              const resize = resizeHandlers[h];
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
                    // Sticky column header — pins to the top of the scroll box so
                    // the columns stay labelled while the rows scroll. The group
                    // headers (in the body) pin just below it (top = HEAD_OFFSET).
                    position: "sticky", top: 0, zIndex: 3,
                    ...(h !== FILLER && { width: widthOf(h) }),
                    ...(!isActions && h !== FILLER && { borderRight: "1px solid" }),
                    ...(isSelect && { "&.MuiTableCell-paddingCheckbox": { width: FIXED_WIDTHS.select } }),
                    ...(isActions && {
                      // The top-right corner: sticky on both axes, above all.
                      right: 0, zIndex: 4,
                      width: 96, minWidth: 96,
                      boxShadow: (t) => `-8px 0 12px -6px ${alpha(t.palette.common.black, t.palette.mode === "dark" ? 0.45 : 0.08)}`,
                    }),
                  }}
                >
                  {isSelect ? (
                    <Checkbox
                      size="small"
                      checked={allChecked}
                      indeterminate={someChecked}
                      onChange={toggleAll}
                      sx={selectableCheckboxSx}
                    />
                  ) : resize ? (
                    <>
                      <Box sx={{ overflow: "hidden", textOverflow: "ellipsis" }}>{h}</Box>
                      <DraggableColResizer
                        onResize={resize.onResize}
                        getCurrentWidth={resize.getCurrentWidth}
                        onResizeEnd={saveWidths}
                        minWidth={120}
                        maxWidth={800}
                        sx={RESIZE_HANDLE_SX}
                      />
                    </>
                  ) : h === FILLER ? null : h}
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
                      // Pinned directly beneath the sticky column header (~28px).
                      // Opaque paper base + the group tint on top, so rows scroll
                      // cleanly under it instead of bleeding through the tint.
                      position: "sticky", top: HEAD_OFFSET, zIndex: 1,
                      bgcolor: "background.paper",
                      backgroundImage: (t) => {
                        const tint = alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05);
                        return `linear-gradient(${tint}, ${tint})`;
                      },
                      borderTop: "1px solid", borderBottom: "1px solid", borderColor: "divider",
                      py: 1.125, px: 2,
                    }}
                  >
                    <Stack direction="row" alignItems="center" spacing={1.25}>
                      <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold", color: "text.primary", flex: 1, minWidth: 0 }}>
                        {section.label}
                      </Typography>
                      <Typography sx={{ typography: "s3", fontWeight: "fontWeightBold", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
                        {/* Under paging the header shows the whole-match count
                            (from the backend's groupCounts) even though only a
                            slice of the group is on this page. */}
                        {(section.totalInGroup ?? section.rows.length)}{" "}
                        {(section.totalInGroup ?? section.rows.length) === 1 ? "scenario" : "scenarios"}
                      </Typography>
                      {onHideGroup && (
                        <Tooltip arrow title={SCENARIOS_COPY.hideGroup}>
                          <IconButton
                            size="small"
                            aria-label={SCENARIOS_COPY.hideGroup}
                            onClick={() => onHideGroup(section.id)}
                            sx={{ p: 0.25, color: "text.subtitle", "&:hover": { color: "text.primary" } }}
                          >
                            <Iconify icon="solar:eye-closed-linear" width={14} />
                          </IconButton>
                        </Tooltip>
                      )}
                    </Stack>
                  </TableCell>
                </TableRow>
              )}

              {section.rows.map((row) => {
                // The scenario's stable place in the whole suite, minted server
                // side — not a per-page counter (it survives filters and paging).
                const idx = row.number;
                const p = row.persona;
                const subTasks = row.subTasks || [];
                const personaSubline = [
                  p?.gender && p.gender.charAt(0).toUpperCase() + p.gender.slice(1),
                  p?.ageGroup,
                  !p?.gender && !p?.ageGroup && p?.role,
                ].filter(Boolean).join(" · ");
                const situationText = row.situation || row.task || "";
                const idealOutcomeText = row.outcome || row.expected || "";

                return (
                  <TableRow key={row.id} hover>
                    {!locked && (
                      <TableCell padding="checkbox" sx={{ pl: 1.5, verticalAlign: "top" }}>
                        <Checkbox
                          size="small"
                          checked={rowChecked(row.id)}
                          onChange={() => toggle(row.id)}
                          sx={selectableCheckboxSx}
                        />
                      </TableCell>
                    )}
                    <TableCell sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums", verticalAlign: "top" }}>
                      {idx}
                    </TableCell>

                    <TableCell sx={{ overflow: "hidden", verticalAlign: "top" }}>
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

                    <TableCell sx={{ overflow: "hidden", verticalAlign: "top" }}>
                      <TruncTooltip title={p?.name || ""}>
                        <Typography noWrap sx={{ typography: "s2" }}>{p?.name}</Typography>
                      </TruncTooltip>
                      {personaSubline && (
                        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{personaSubline}</Typography>
                      )}
                    </TableCell>

                    <TableCell sx={{ overflow: "hidden", verticalAlign: "top" }}>
                      <ClampCell text={situationText} lines={limits.lines} />
                    </TableCell>

                    <TableCell sx={{ overflow: "hidden", verticalAlign: "top" }}>
                      <SubTasksCell subTasks={subTasks} limit={limits.subTasks} />
                    </TableCell>

                    <TableCell sx={{ overflow: "hidden", verticalAlign: "top" }}>
                      <ClampCell text={idealOutcomeText} lines={limits.lines} />
                    </TableCell>

                    <TableCell />

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
                      <Tooltip arrow title={locked ? LOCK_TOOLTIP : SCENARIOS_COPY.editLabel}>
                        <Box component="span" sx={{ display: "inline-flex" }}>
                          <IconButton size="small" disabled={locked} aria-label={SCENARIOS_COPY.editLabel} onClick={() => onEdit?.(row)}>
                            <Iconify icon="solar:pen-new-square-linear" width={15} sx={{ color: "text.subtitle" }} />
                          </IconButton>
                        </Box>
                      </Tooltip>
                      <Tooltip arrow title={locked ? LOCK_TOOLTIP : SCENARIOS_COPY.removeLabel}>
                        <Box component="span" sx={{ display: "inline-flex" }}>
                          <IconButton size="small" disabled={locked} aria-label={SCENARIOS_COPY.removeLabel} onClick={() => onRemove(row.id)}>
                            <Iconify icon="solar:trash-bin-trash-linear" width={15} sx={{ color: "text.subtitle" }} />
                          </IconButton>
                        </Box>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                );
              })}
            </Fragment>
          ))}
        </TableBody>
      </Table>
  );
}

ScenarioTable.propTypes = {
  rows: PropTypes.arrayOf(SCENARIO_SHAPE).isRequired,
  groups: PropTypes.arrayOf(GROUP_SHAPE),
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  onHideGroup: PropTypes.func,
  selectedIds: PropTypes.arrayOf(PropTypes.string),
  onSelectionChange: PropTypes.func,
  // Predicate selection model (useSelection). When present it drives the
  // checkboxes and supersedes selectedIds/onSelectionChange.
  selection: PropTypes.object,
  pageIds: PropTypes.arrayOf(PropTypes.string),
  onTogglePage: PropTypes.func,
  locked: PropTypes.bool,
  rowHeight: PropTypes.oneOf(Object.keys(ROW_HEIGHTS)),
};
