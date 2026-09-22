import PropTypes from "prop-types";
import { Fragment, useEffect, useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  IconButton, Tooltip, Checkbox,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { subTasksFor } from "../../_mock/contract";
import { ProvenanceGlyph } from "../ScenariosStep";
import { sourceOf, relativeTime } from "../../_mock/scenarioProvenance";
import { admissionOf } from "../../_mock/coverage";

/* Neutral white-on-selected checkbox — no primary colour, keeps the
   table's monochrome treatment. */
const selectableCheckboxSx = {
  p: 0,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

/**
 * Scenarios as a table.
 *
 * The list view answers "what is this scenario" — it expands into the brief,
 * the checks and the proof that the task is passable. The table answers a
 * different question: "what is in here, and what is missing". Every scenario
 * is one line, the derived axes are columns, and thirty rows are comparable
 * without opening any of them.
 *
 * Same rows, same derivations as the coverage matrix — nothing here is a
 * second source of truth.
 */
export default function ScenarioTable({ rows, groups, env, onEdit, onRemove, onHideGroup, selectedIds, onSelectionChange, locked = false }) {
  /*
    Two shapes come in: pre-grouped (list view mirror) or a flat rows
    array (fallback). If groups are given, render section-header rows
    between them so the table reads the same use-case story as the
    list; if not, fall back to a flat table.
  */
  const sections = groups?.length
    ? groups
    : [{ id: "all", label: null, rows: rows || [] }];

  /* All scenario ids across every section — used to compute the
     header-checkbox tri-state (checked / indeterminate / unchecked)
     when the user wants a select-all shortcut on the current view. */
  const allIds = useMemo(
    () => sections.flatMap((s) => (s.rows || []).map((r) => r.id)).filter(Boolean),
    [sections],
  );

  /*
    Selection is a bulk-action affordance, not a run gate — every
    scenario in the table runs on the next simulation regardless of
    checked state. So the table opens with nothing selected; the
    checkboxes only light up when the user wants to Delete or edit a
    group of rows together (via the builder chat on the left).

    Controlled from the parent so the SelectionBar + workspace chat
    stay in sync. Falls back to internal state when unwired.
  */
  const [internalSelected, setInternalSelected] = useState(() => new Set());
  const isControlled = Array.isArray(selectedIds);
  const selected = isControlled ? new Set(selectedIds) : internalSelected;

  const commit = (next) => {
    if (isControlled) {
      onSelectionChange?.(Array.from(next));
    } else {
      setInternalSelected(next);
      onSelectionChange?.(Array.from(next));
    }
  };

  /*
    Drop selection ids for rows that no longer exist. Only runs in
    the UNCONTROLLED case — when the parent owns the selection
    state (batches rendered per-timeline-node, all sharing the
    same selectedIds bag), each ScenarioTable only knows its own
    batch's ids. Running cleanup here would wipe out selections
    made in sibling batches (any id not in THIS batch would be
    stripped), which broke cross-batch selection.
    In controlled mode, the parent (ScenariosStep) is responsible
    for pruning stale ids across the full scenario set.
  */
  useEffect(() => {
    if (isControlled) return;
    const cleaned = Array.from(selected).filter((id) => allIds.includes(id));
    if (cleaned.length !== selected.size) commit(new Set(cleaned));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allIds.join("|")]);

  const toggle = (id) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id); else next.add(id);
    commit(next);
  };
  /*
    Select-all in a batch header only affects THIS batch's rows —
    other batches' selections stay intact. `thisBatchSelectedCount`
    is the count of currently-selected rows that belong to this
    table (not the total across every batch), so the header
    checkbox tri-state reflects only this batch.
  */
  const thisBatchSelectedCount = allIds.filter((id) => selected.has(id)).length;
  const toggleAll = () => {
    const allSelectedHere = thisBatchSelectedCount === allIds.length && allIds.length > 0;
    const next = new Set(selected);
    if (allSelectedHere) {
      allIds.forEach((id) => next.delete(id));
    } else {
      allIds.forEach((id) => next.add(id));
    }
    commit(next);
  };
  const allChecked = allIds.length > 0 && thisBatchSelectedCount === allIds.length;
  const someChecked = thisBatchSelectedCount > 0 && thisBatchSelectedCount < allIds.length;

  /*
    Column order (final): checkbox, scenario, persona, situation, sub-tasks,
    branch category, ideal outcome. "Conversation branch" was
    dropped — it duplicated what "Branch category" already carries at
    a scannable level, and it was the widest, monospace-heaviest
    column on the table. "Outcome" is renamed "Ideal outcome" so
    it's obvious the column describes what a good result looks like,
    not a run's actual result.
  */
  const columns = [
    "select", "#", "Scenario", "Persona", "Situation",
    "Sub-goals", "Ideal outcome", "",
  ];
  let counter = 0;

  return (
    <Box sx={{ overflowX: "auto" }}>
      <Table size="small" sx={{ minWidth: 1400 }}>
        <TableHead>
          <TableRow>
            {columns.map((h, i) => {
              /*
                The last (empty-label) column is the row-actions column.
                Pin it to the right edge with sticky positioning so the
                edit / remove icons stay visible no matter how wide the
                situation/outcome/branch columns push the table. Header
                and body cells share the same sticky rule + shadow so
                the pinned column reads as one thing.
              */
              const isActions = i === columns.length - 1;
              const isSelect = h === "select";
              return (
                <TableCell
                  key={h || i}
                  align={isActions ? "right" : "left"}
                  padding={isSelect ? "checkbox" : "normal"}
                  sx={{
                    typography: "s3", fontWeight: 700, color: "text.subtitle",
                    textTransform: "uppercase", letterSpacing: .4,
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
                      checked={allChecked}
                      indeterminate={someChecked}
                      onChange={toggleAll}
                      disabled={locked}
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
                      bgcolor: (t) => alpha(
                        t.palette.text.primary,
                        t.palette.mode === "dark" ? 0.06 : 0.04,
                      ),
                      borderBottom: "1px solid", borderColor: "divider",
                      py: 1, px: 2,
                    }}
                  >
                    <Stack direction="row" alignItems="center" spacing={1.25}>
                      <Iconify icon="solar:alt-arrow-down-linear" width={11} sx={{ color: "text.subtitle" }} />
                      <Typography sx={{ typography: "s2", fontWeight: 600, color: "text.primary", flex: 1, minWidth: 0, fontSize: 12.5 }}>
                        {section.label}
                      </Typography>
                      <Typography sx={{ typography: "s3", fontWeight: 600, color: "text.subtitle", fontVariantNumeric: "tabular-nums", fontSize: 11 }}>
                        {section.rows.length}
                      </Typography>
                      {onHideGroup && (
                        <Tooltip arrow title="Hide this group">
                          <IconButton
                            size="small"
                            onClick={(e) => { e.stopPropagation(); onHideGroup(section.id); }}
                            sx={{ p: 0.25, color: "text.subtitle", "&:hover": { color: "text.primary" } }}
                          >
                            <Iconify icon="solar:eye-closed-linear" width={12} />
                          </IconButton>
                        </Tooltip>
                      )}
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
                  !p?.gender && !p?.ageGroup && p?.role, /* fall back to role for requesters */
                ].filter(Boolean).join(" · ");
                const situationText = row.situation || row.task || "";
                const idealOutcomeText = row.outcome || row.expected || "";

                return (
                  <TableRow key={row.id} hover>
                    <TableCell padding="checkbox" sx={{ pl: 1.5, verticalAlign: "top" }}>
                      <Checkbox
                        size="small"
                        checked={selected.has(row.id)}
                        onChange={() => toggle(row.id)}
                        disabled={locked}
                        sx={selectableCheckboxSx}
                      />
                    </TableCell>
                    <TableCell sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums", verticalAlign: "top" }}>
                      {idx}
                    </TableCell>

                {/* SCENARIO — name (bold, truncated) + summary (subtle,
                    truncated). Both wrapped in tooltips so long values
                    are readable on hover. */}
                <TableCell sx={{ maxWidth: 280, verticalAlign: "top" }}>
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    <TruncTooltip title={row.name || row.title}>
                      <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{row.name || row.title}</Typography>
                    </TruncTooltip>
                    {row.provedBroke && (
                      <Tooltip arrow title="Broke when the env changed — the proof no longer holds.">
                        <Box
                          sx={{
                            display: "inline-flex", alignItems: "center", gap: 0.375,
                            height: 16, px: 0.5, borderRadius: 0.5,
                            border: (t) => `1px solid ${alpha("#DC2626", 0.4)}`,
                            color: "#DC2626",
                            bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.14 : 0.08),
                          }}
                        >
                          <Iconify icon="solar:danger-triangle-bold" width={10} />
                          <Typography sx={{ typography: "s3", fontWeight: 700, letterSpacing: 0.3 }}>
                            Broken
                          </Typography>
                        </Box>
                      </Tooltip>
                    )}
                    {row.critical && (
                      <Tooltip arrow title="Critical — a failure here is a release blocker">
                        <Box sx={{ display: "flex" }}>
                          <Iconify icon="solar:danger-triangle-bold" width={13} sx={{ color: "#DC2626" }} />
                        </Box>
                      </Tooltip>
                    )}
                    {/* PRD §9 AC-9.13 — admission status. Amber icon only,
                        matching the existing red-triangle "critical" idiom
                        so the row doesn't grow a text chip on every case.
                        Tooltip surfaces the compiler's specific reason. */}
                    {(() => {
                      const adm = admissionOf(row);
                      if (adm.admitted) return null;
                      return (
                        <Tooltip arrow title={`Quarantined — ${adm.reason}`}>
                          <Box sx={{ display: "flex" }}>
                            <Iconify icon="solar:shield-cross-bold" width={13} sx={{ color: "#CA8A04" }} />
                          </Box>
                        </Tooltip>
                      );
                    })()}
                    {row.twinSeedPrompt && (
                      <Tooltip arrow title={`Twin seed override: "${row.twinSeedPrompt.slice(0, 140)}${row.twinSeedPrompt.length > 140 ? "…" : ""}"`}>
                        <Box sx={{ display: "flex" }}>
                          <Iconify icon="solar:server-square-linear" width={12} sx={{ color: "#7857FC" }} />
                        </Box>
                      </Tooltip>
                    )}
                  </Stack>
                  <TruncTooltip title={row.summary || row.title}>
                    <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{row.summary || row.title}</Typography>
                  </TruncTooltip>
                </TableCell>

                {/* PERSONA — name + gender/age line */}
                <TableCell sx={{ maxWidth: 200, verticalAlign: "top" }}>
                  <TruncTooltip title={p?.name || ""}>
                    <Typography noWrap sx={{ typography: "s2" }}>{p?.name}</Typography>
                  </TruncTooltip>
                  {personaSubline && (
                    <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{personaSubline}</Typography>
                  )}
                </TableCell>

                {/* SITUATION — clamped to 3 lines, full text on hover
                    tooltip so a long paragraph is still readable
                    without breaking the row height. */}
                <TableCell sx={{ maxWidth: 320, verticalAlign: "top" }}>
                  <ClampCell text={situationText} />
                </TableCell>

                {/* SUB-TASKS — moved up to sit next to the persona /
                    situation columns because it describes the *shape*
                    of the task, not its result. Same 3-row cap with a
                    hover popover for the full list. */}
                <TableCell sx={{ maxWidth: 260, verticalAlign: "top" }}>
                  <SubTasksCell subTasks={subTasks} />
                </TableCell>

                {/* IDEAL OUTCOME — clamped to 3 lines, tooltip on
                    hover. Renamed from "Outcome" so it clearly
                    describes the criterion, not what a specific run
                    actually did. */}
                <TableCell sx={{ maxWidth: 320, verticalAlign: "top" }}>
                  <ClampCell text={idealOutcomeText} />
                </TableCell>

                <TableCell
                  align="right"
                  sx={{
                    whiteSpace: "nowrap", verticalAlign: "top",
                    position: "sticky", right: 0, zIndex: 1,
                    width: 96, minWidth: 96,
                    /*
                      Sticky cells need an opaque background to hide the
                      columns scrolling underneath. Layering the two on
                      backgroundImage keeps the row-hover tint visually
                      identical to the rest of the row: same
                      action.hover overlay, same paper base — no more
                      mismatched patch on hover.
                    */
                    bgcolor: "background.paper",
                    boxShadow: (t) => `-8px 0 12px -6px ${alpha(t.palette.common.black, t.palette.mode === "dark" ? 0.45 : 0.08)}`,
                    transition: "background-image 120ms ease",
                    ".MuiTableRow-hover:hover &": {
                      backgroundImage: (t) => `linear-gradient(${t.palette.action.hover}, ${t.palette.action.hover})`,
                    },
                  }}
                >
                  <Tooltip arrow title={locked ? "Fork this environment to edit." : "Edit scenario"}>
                    <span>
                      <IconButton size="small" disabled={locked} onClick={() => onEdit(row)}>
                        <Iconify icon="solar:pen-new-square-linear" width={15} sx={{ color: "text.subtitle" }} />
                      </IconButton>
                    </span>
                  </Tooltip>
                  <Tooltip arrow title={locked ? "Fork this environment to edit." : "Remove from this environment"}>
                    <span>
                      <IconButton size="small" disabled={locked} onClick={() => onRemove(row.id)}>
                        <Iconify icon="solar:trash-bin-trash-linear" width={15} sx={{ color: "text.subtitle" }} />
                      </IconButton>
                    </span>
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
  rows: PropTypes.array.isRequired,
  groups: PropTypes.array,
  env: PropTypes.object,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  onHideGroup: PropTypes.func,
  selectedIds: PropTypes.array,
  onSelectionChange: PropTypes.func,
};

/* ── readability helpers ──────────────────────────────────────────────────── */

/**
 * Attach a tooltip to a truncated line so hovering reveals the full
 * value. The tooltip only actually pops when the child overflows,
 * so short values don't get a noisy tooltip on every hover.
 *
 * Kept purposefully thin — the tooltip lives at the row level, not
 * per-Typography, so the same wrapper can gate both single-line
 * `noWrap` labels and multi-line clamped bodies.
 */
function TruncTooltip({ title, children }) {
  if (!title) return children;
  return (
    <Tooltip
      arrow
      enterDelay={300}
      title={
        <Box sx={{ typography: "s3", whiteSpace: "pre-wrap", wordBreak: "break-word", maxWidth: 460 }}>
          {title}
        </Box>
      }
    >
      <Box sx={{ minWidth: 0 }}>
        {children}
      </Box>
    </Tooltip>
  );
}
TruncTooltip.propTypes = { title: PropTypes.node, children: PropTypes.node };

/**
 * Multi-line text cell — clamped to 3 lines so the row stays a
 * predictable height, but full content is one hover away in a
 * pre-formatted tooltip. Empty values render as an em-dash so the
 * column doesn't visually collapse.
 */
function ClampCell({ text }) {
  const value = text || "—";
  return (
    <TruncTooltip title={text}>
      <Typography sx={{
        typography: "s2", color: "text.secondary", lineHeight: 1.45,
        display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical",
        overflow: "hidden", wordBreak: "break-word",
      }}>
        {value}
      </Typography>
    </TruncTooltip>
  );
}
ClampCell.propTypes = { text: PropTypes.string };

/**
 * Sub-tasks column body. Shows up to 3 sub-tasks inline; anything
 * past that is summarised as "+ N more". Hovering the row reveals
 * the full numbered list, so a big scenario's twelve sub-tasks stay
 * discoverable without turning every table row into a scroll well.
 */
/*
  SubTasksCell accepts both shapes callers actually pass:
  - `[{ id, label }, ...]` — the canonical shape produced by
    subTasksFor() and the template scenarios
  - `["step one", "step two", ...]` — plain-string arrays produced
    by the scratch derivation and older mocks
  Coerces to a common `{ id, label }` shape up front so no caller
  silently renders bare "1. 2. 3." numbers with the label missing.
*/
function normaliseSubTasks(subTasks) {
  return (subTasks || []).map((st, i) => {
    if (typeof st === "string") return { id: `st-${i}`, label: st };
    if (!st) return null;
    return { id: st.id || `st-${i}`, label: st.label || st.text || st.title || "" };
  }).filter((st) => st && st.label);
}

function SubTasksCell({ subTasks }) {
  const list = normaliseSubTasks(subTasks);
  if (!list.length) {
    return <Typography sx={{ typography: "s3", color: "text.subtitle" }}>—</Typography>;
  }
  const fullList = list.map((st, i) => `${i + 1}. ${st.label}`).join("\n");
  return (
    <TruncTooltip title={fullList}>
      <Stack spacing={0.375}>
        {list.slice(0, 3).map((st, i) => (
          <Stack key={st.id} direction="row" spacing={0.75} alignItems="flex-start">
            <Typography sx={{
              typography: "s3", color: "text.subtitle",
              fontVariantNumeric: "tabular-nums", flexShrink: 0, mt: "1px",
            }}>
              {i + 1}.
            </Typography>
            <Typography noWrap sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}>
              {st.label}
            </Typography>
          </Stack>
        ))}
        {list.length > 3 && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", pl: 1.75 }}>
            + {list.length - 3} more
          </Typography>
        )}
      </Stack>
    </TruncTooltip>
  );
}
SubTasksCell.propTypes = { subTasks: PropTypes.array };
