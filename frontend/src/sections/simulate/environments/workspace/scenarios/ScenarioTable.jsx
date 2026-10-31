import PropTypes from "prop-types";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  IconButton, Tooltip, Checkbox, Menu, MenuItem, Button,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import DraggableColResizer from "src/components/draggable-col-resizer";
import { getStorage, setStorage } from "src/hooks/use-local-storage";
import { BUILD_TONES } from "../../buildEnvironment/buildTones";
import SoonChip from "../../components/SoonChip";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";
import { ClampCell, TruncTooltip, UnsavedMark } from "./ScenarioTableCells";
import {
  ROW_HEIGHTS,
  SCENARIOS_COPY,
  isUnsavedAddition,
  unsavedCheckboxProps,
} from "./scenarios.constants";
import { personaSubline } from "./personaLabels";
import { GROUP_SHAPE, SCENARIO_SHAPE } from "./scenarios.shapes";

// A seeded-from-template env is read-only until forked; every mutating control
// on the table carries this on a tooltip so the disabled state reads as
// intentional rather than broken.
const LOCK_TOOLTIP = "Fork this environment to edit.";

// Body cells pinned to an edge need an opaque base (rows scroll under them)
// and the row's hover tint layered on top. Only the actions column on the
// right casts a shadow; the checkbox column sits flat beside the row.
const pinnedBodyCellSx = (side) => ({
  position: "sticky", [side]: 0, zIndex: 1,
  bgcolor: "background.paper",
  ...(side === "right" && {
    boxShadow: (t) => `-8px 0 12px -6px ${alpha(t.palette.common.black, t.palette.mode === "dark" ? 0.45 : 0.08)}`,
  }),
  transition: "background-image 120ms ease",
  ".MuiTableRow-hover:hover &": {
    backgroundImage: (t) => `linear-gradient(${t.palette.action.hover}, ${t.palette.action.hover})`,
  },
});
const pinnedSelectCellSx = { ...pinnedBodyCellSx("left"), pl: 1.5, verticalAlign: "top" };

// Height of the sticky column-header row, set on its cells. The group headers
// pin at this offset so they rest just under it, not on top.
const HEAD_OFFSET = 36;

const WIDTHS_KEY = "simulate:scenario-table:column-widths";
const DEFAULT_WIDTHS = { Scenario: 380, Persona: 190, Brief: 340, "Passes when": 260 };
const FIXED_WIDTHS = { select: 48, "#": 44, "Sub-goals": 88, "Must-pass": 92, "Last runs": 116, "": 48 };

// Vel's column headings. What a column needs that the API doesn't send yet is
// a soon chip in its header: a scenario's state and source, whether it's
// must-pass, and how it did in the last runs.
const HEAD_TIPS = {
  Scenario: "The summary; the scenario's key is on hover",
  "Sub-goals": "How many sub-goals the scenario is graded on",
  Brief: "What the caller is told to do",
  "Passes when": "What the agent has to do for the scenario to pass",
};
function HeadLabel({ h }) {
  if (h === "Scenario")
    return (
      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
        <Box component="span">Scenario</Box>
        <SoonChip label="State" detail="Whether a scenario is valid, needs a re-check after a change, is outdated, stale or archived." what="A scenario's state, like needs review or stale" size="tiny" />
        <SoonChip label="Source" detail="Whether a scenario came from the build, the builder chat, was written by hand, or was imported (Error Feed, production, dataset or script)." what="Where a scenario came from: the build, chat, by hand or an import" size="tiny" />
      </Stack>
    );
  if (h === "Must-pass") return <SoonChip label="Must-pass" detail="A must-pass scenario is a release blocker: an improvement that makes it fail is rejected." what="Scenarios an improvement may never break" size="tiny" />;
  if (h === "Last runs") return <SoonChip label="Last runs" detail="How this scenario did in the environment's most recent runs, oldest to latest." what="This scenario in the environment's last runs" plural size="tiny" />;
  return (
    <Tooltip arrow title={HEAD_TIPS[h] || ""} disableHoverListener={!HEAD_TIPS[h]}>
      <Box component="span" sx={{ overflow: "hidden", textOverflow: "ellipsis" }}>{h}</Box>
    </Tooltip>
  );
}
HeadLabel.propTypes = { h: PropTypes.string };

// Name · age group, as Vel's Persona column reads; gender stays on hover.
const personaLine = (p) => [p?.name, p?.ageGroup].filter(Boolean).join(" · ");
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

// Stop a click inside a cell (checkbox, row actions) from also opening the row.
const stopRowClick = (e) => e.stopPropagation();

// The per-row edit pencil opens the scenario editor through onEdit; a click
// anywhere else on the row opens it in full through onOpen. Stripped from the
// source: the twin-seed override icon (twinSeedPrompt) — twin-only, not on
// either entry path.
export default function ScenarioTable({ rows, groups, onEdit, onRemove, onOpen, onHideGroup, selectedIds, onSelectionChange, selection, pageIds, onTogglePage, locked = false, rowHeight = "Short" }) {
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
  // Vel groups the list by who added each batch. The API has no batches or
  // authors, so sample authors wrap the contract's own use-case groups: the
  // first group as the latest batch, the rest as an earlier one. Flagged UI
  // only; hiding a batch is local.
  const batches = useMemo(() => batchesOf(sections), [sections]);
  const [hiddenBatches, setHiddenBatches] = useState(() => new Set());
  const toggleBatch = (id) =>
    setHiddenBatches((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

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
    "#", "Scenario", "Persona", "Sub-goals", "Must-pass", "Last runs", "Brief", "Passes when", FILLER, "",
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
                    typography: "s3", fontWeight: "fontWeightSemiBold", color: "text.subtitle",
                    bgcolor: "background.neutral",
                    borderBottom: "1px solid", borderColor: "divider",
                    whiteSpace: "nowrap", height: HEAD_OFFSET,
                    // Sticky column header — pins to the top of the scroll box so
                    // the columns stay labelled while the rows scroll. The group
                    // headers (in the body) pin just below it (top = HEAD_OFFSET).
                    position: "sticky", top: 0, zIndex: 3,
                    ...(h !== FILLER && { width: widthOf(h) }),
                    ...(!isActions && h !== FILLER && { borderRight: "1px solid" }),
                    ...(isSelect && {
                      "&.MuiTableCell-paddingCheckbox": { width: FIXED_WIDTHS.select },
                      // The top-left corner: sticky on both axes, above all.
                      left: 0, zIndex: 4,
                    }),
                    ...(isActions && {
                      // The top-right corner: sticky on both axes, above all.
                      right: 0, zIndex: 4,
                      width: 48, minWidth: 48,
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
                      <HeadLabel h={h} />
                      <DraggableColResizer
                        onResize={resize.onResize}
                        getCurrentWidth={resize.getCurrentWidth}
                        onResizeEnd={saveWidths}
                        minWidth={120}
                        maxWidth={800}
                        sx={RESIZE_HANDLE_SX}
                      />
                    </>
                  ) : h === FILLER || isActions ? null : <HeadLabel h={h} />}
                </TableCell>
              );
            })}
          </TableRow>
        </TableHead>

        <TableBody>
          {batches.map((batch) => (
            <Fragment key={batch.id}>
              <BatchHeaderRow
                batch={batch}
                colSpan={columns.length}
                hidden={hiddenBatches.has(batch.id)}
                onToggle={() => toggleBatch(batch.id)}
              />
              {!hiddenBatches.has(batch.id) && (
                <>
          {batch.sections.map((section) => (
            <Fragment key={section.id}>
              {section.label && (
                <TableRow>
                  <TableCell
                    colSpan={columns.length}
                    sx={{
                      // Pinned directly beneath the sticky column header.
                      // Opaque paper base + the group tint on top, so rows scroll
                      // cleanly under it instead of bleeding through the tint.
                      // Above the pinned checkbox and action cells, which scroll
                      // under it too.
                      position: "sticky", top: HEAD_OFFSET, zIndex: 2,
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
                const personaHover = [p?.name, personaSubline(p)].filter(Boolean).join(" · ");
                const briefText = row.situation || row.task || "";
                const passesText = row.outcome || row.expected || "";
                const key = row.name || row.title;
                return (
                  <TableRow
                    key={row.id}
                    hover
                    onClick={onOpen ? () => onOpen(row) : undefined}
                    // Reachable from the keyboard too: Tab to the row, Enter opens it.
                    tabIndex={onOpen ? 0 : undefined}
                    onKeyDown={onOpen ? (e) => {
                      if (e.target !== e.currentTarget || e.key !== "Enter") return;
                      e.preventDefault();
                      onOpen(row);
                    } : undefined}
                    aria-label={onOpen ? SCENARIOS_COPY.openRow(row.name || row.title) : undefined}
                    sx={onOpen ? {
                      cursor: "pointer",
                      "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main", outlineOffset: -2 },
                    } : undefined}
                  >
                    {!locked && (
                      // Pinned to the left edge so the selection stays in reach
                      // while the table scrolls sideways.
                      <TableCell padding="checkbox" sx={pinnedSelectCellSx} onClick={stopRowClick}>
                        <Checkbox
                          size="small"
                          checked={!isUnsavedAddition(row) && rowChecked(row.id)}
                          {...unsavedCheckboxProps(row)}
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
                        <Tooltip arrow title={key || ""} placement="top-start">
                          <Typography noWrap sx={{ typography: "s2", fontWeight: "fontWeightSemiBold", minWidth: 0 }}>
                            {row.summary || row.title || key}
                          </Typography>
                        </Tooltip>
                        {row.critical && (
                          <Tooltip arrow title={SCENARIOS_COPY.critical}>
                            <Box sx={{ display: "flex" }}>
                              <Iconify icon="solar:danger-triangle-bold" width={13} sx={{ color: BUILD_TONES.red }} />
                            </Box>
                          </Tooltip>
                        )}
                        <UnsavedMark row={row} />
                      </Stack>
                    </TableCell>
                    <TableCell sx={{ overflow: "hidden", verticalAlign: "top" }}>
                      <TruncTooltip title={personaHover}>
                        <Typography noWrap sx={{ typography: "s2" }}>{personaLine(p)}</Typography>
                      </TruncTooltip>
                    </TableCell>
                    <TableCell align="right" sx={{ typography: "s2", fontVariantNumeric: "tabular-nums", verticalAlign: "top" }}>
                      <Tooltip arrow title={subTasks.join(" · ")} disableHoverListener={!subTasks.length}>
                        <Box component="span">{subTasks.length || "-"}</Box>
                      </Tooltip>
                    </TableCell>
                    <TableCell align="center" onClick={stopRowClick} sx={{ verticalAlign: "top", py: 0.5 }}>
                      <MustPassToggle name={key} disabled={locked} />
                    </TableCell>
                    <TableCell sx={{ typography: "s3", color: "text.disabled", verticalAlign: "top" }}>-</TableCell>
                    <TableCell sx={{ overflow: "hidden", verticalAlign: "top" }}>
                      <ClampCell text={briefText} lines={limits.lines} />
                    </TableCell>
                    <TableCell sx={{ overflow: "hidden", verticalAlign: "top" }}>
                      <ClampCell text={passesText} lines={limits.lines} />
                    </TableCell>
                    <TableCell />

                    <TableCell
                      align="right"
                      onClick={stopRowClick}
                      sx={{
                        ...pinnedBodyCellSx("right"),
                        whiteSpace: "nowrap", verticalAlign: "top",
                        width: 48, minWidth: 48,
                      }}
                    >
                      <RowMenu row={row} name={key} locked={locked} onEdit={onEdit} onRemove={onRemove} />
                    </TableCell>
                  </TableRow>
                );
              })}
            </Fragment>
          ))}
                </>
              )}
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
  // Opens a row in full (the scenario drawer).
  onOpen: PropTypes.func,
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


const uiOnly = () => enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });

// Vel's must-pass mark. Whether a scenario is must-pass isn't in the API, so
// it toggles here and saves nothing.
function MustPassToggle({ name, disabled }) {
  const [on, setOn] = useState(false);
  return (
    <IconButton
      size="small"
      disabled={disabled}
      aria-pressed={on}
      aria-label={`Must-pass for ${name}`}
      onClick={() => {
        setOn((v) => !v);
        uiOnly();
      }}
      sx={{ p: 0.25, color: on ? "text.primary" : "text.disabled" }}
    >
      <Iconify icon={on ? "solar:shield-check-bold" : "solar:shield-linear"} width={16} />
    </IconButton>
  );
}
MustPassToggle.propTypes = { name: PropTypes.string, disabled: PropTypes.bool };

// Vel's row menu: Edit, Archive and Remove. Archive has no API, so it's
// flagged and saves nothing; Remove still asks first (the parent confirms).
function RowMenu({ row, name, locked, onEdit, onRemove }) {
  const [anchor, setAnchor] = useState(null);
  const close = () => setAnchor(null);
  return (
    <>
      <Tooltip arrow title={locked ? LOCK_TOOLTIP : ""} disableHoverListener={!locked}>
        <Box component="span" sx={{ display: "inline-flex" }}>
          <IconButton size="small" disabled={locked} aria-label={`More actions for ${name}`} onClick={(e) => setAnchor(e.currentTarget)}>
            <Iconify icon="solar:menu-dots-bold" width={16} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </Box>
      </Tooltip>
      <Menu
        open={!!anchor}
        anchorEl={anchor}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <MenuItem
          onClick={() => {
            close();
            onEdit?.(row);
          }}
          sx={{ typography: "s2", gap: 1 }}
        >
          <Iconify icon="solar:pen-new-square-linear" width={16} />
          Edit
        </MenuItem>
        <MenuItem
          onClick={() => {
            close();
            uiOnly();
          }}
          sx={{ typography: "s2", gap: 1 }}
        >
          <Iconify icon="solar:archive-linear" width={16} />
          Archive
          <UiOnlyFlag feature="scenarioStates" sx={{ ml: 0.5 }} />
        </MenuItem>
        <MenuItem
          onClick={() => {
            close();
            onRemove?.(row.id);
          }}
          sx={{ typography: "s2", gap: 1, color: "error.main" }}
        >
          <Iconify icon="solar:trash-bin-trash-linear" width={16} />
          Remove
        </MenuItem>
      </Menu>
    </>
  );
}
RowMenu.propTypes = {
  row: PropTypes.object.isRequired,
  name: PropTypes.string,
  locked: PropTypes.bool,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
};

const SAMPLE_AUTHORS = [
  { initials: "AK", name: "Arjun Kapoor", how: "By hand", icon: "solar:pen-linear", ago: "5h ago", latest: true },
  { initials: "V", name: "Vel", how: "In chat", icon: "solar:chat-round-line-linear", ago: "4d ago" },
];
const countOf = (section) => section.totalInGroup ?? section.rows.length;
function batchesOf(sections) {
  const parts = sections.length > 1 ? [sections.slice(0, 1), sections.slice(1)] : [sections];
  return parts.map((secs, i) => ({
    id: `batch-${i}`,
    author: SAMPLE_AUTHORS[i],
    sections: secs,
    count: secs.reduce((n, sec) => n + countOf(sec), 0),
  }));
}

function BatchHeaderRow({ batch, colSpan, hidden, onToggle }) {
  const { author, count } = batch;
  return (
    <TableRow>
      <TableCell colSpan={colSpan} sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider", bgcolor: "background.paper" }}>
        <Stack direction="row" alignItems="center" spacing={1.25} sx={{ position: "sticky", left: 16, width: "fit-content" }}>
          <Box
            sx={{
              width: 28, height: 28, borderRadius: "50%", flexShrink: 0, display: "grid", placeItems: "center",
              bgcolor: "background.neutral", typography: "s3", fontWeight: 600, color: "text.secondary",
            }}
          >
            {author.initials}
          </Box>
          <Box minWidth={0}>
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                {`${author.name} added ${count} scenario${count === 1 ? "" : "s"}`}
              </Typography>
              {author.latest && (
                <Box component="span" sx={{ px: 0.75, borderRadius: 0.5, typography: "s3", bgcolor: "background.neutral", color: "text.secondary" }}>
                  Latest
                </Box>
              )}
              <UiOnlyFlag feature="scenarioBatches" />
            </Stack>
            <Stack direction="row" alignItems="center" spacing={0.5} sx={{ color: "text.subtitle" }}>
              <Iconify icon={author.icon} width={12} />
              <Typography sx={{ typography: "s3", color: "inherit" }}>{`${author.how} · ${author.ago}`}</Typography>
            </Stack>
          </Box>
          <Button
            size="small"
            variant="outlined"
            onClick={onToggle}
            endIcon={<Iconify icon={hidden ? "solar:alt-arrow-down-linear" : "solar:alt-arrow-up-linear"} width={12} />}
            sx={{ flexShrink: 0, ml: 1.5, color: "text.primary", borderColor: "divider" }}
          >
            {`${hidden ? "Show" : "Hide"} ${count}`}
          </Button>
        </Stack>
      </TableCell>
    </TableRow>
  );
}
BatchHeaderRow.propTypes = {
  batch: PropTypes.object.isRequired,
  colSpan: PropTypes.number.isRequired,
  hidden: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
};
