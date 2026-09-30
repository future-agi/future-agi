import PropTypes from "prop-types";
import { Fragment, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  IconButton, Tooltip, Checkbox, CircularProgress,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { subTasksFor } from "../../_mock/contract";
import { admissionOf } from "../../_mock/coverage";
import { versionNumber } from "../../_mock/versions";
import { ScenarioRowContext } from "./scenarioRowContext";
import BlockerFlag from "./BlockerFlag";

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
/* A scenario a later rebuild added is not part of an older pinned version. */
const laterThanPinned = (row, envVersion) => !!(row?.addedInEnv && envVersion
  && versionNumber(row.addedInEnv) > versionNumber(envVersion));

function StatusChip({ status }) {
  const tone = status.tone;
  return (
    <Tooltip arrow title={status.detail || ""}>
      <Box
        sx={{
          display: "inline-flex", alignItems: "center", gap: 0.625,
          height: 22, px: 0.875, borderRadius: 0.75, border: "1px solid",
          borderColor: tone ? alpha(tone, 0.4) : "divider",
          bgcolor: (t) => (tone ? alpha(tone, t.palette.mode === "dark" ? 0.12 : 0.06) : "transparent"),
          color: tone || "text.secondary",
        }}
      >
        {status.id === "proved" && <Iconify icon="solar:check-circle-linear" width={12} sx={{ color: "text.disabled" }} />}
        {status.id === "checking" && <CircularProgress size={10} thickness={6} sx={{ color: "text.disabled" }} />}
        <Typography noWrap sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}>{status.label}</Typography>
      </Box>
    </Tooltip>
  );
}
StatusChip.propTypes = { status: PropTypes.object.isRequired };

/*
  The tools a scenario needs, read against both sides of the pairing:
    the world can't answer it   → amber: the scenario comes back not measured
    the agent doesn't call it   → dashed: the scenario still runs, and
                                  measures that gap in the agent
*/
function ToolChips({ tools, answers, agentCalls, agentLabel, envVersion }) {
  if (!tools.length) {
    return (
      <Tooltip arrow title="Not tied to one tool — it tests how the agent behaves across the call.">
        <Typography sx={{ typography: "s3", color: "text.disabled" }}>Any</Typography>
      </Tooltip>
    );
  }
  return (
    <Stack direction="row" spacing={0.5} rowGap={0.5} flexWrap="wrap">
      {tools.map((t) => {
        const unanswered = answers && !answers.has(t);
        const notCalled = !unanswered && agentCalls && !agentCalls.has(t);
        const tip = unanswered
          ? `Environment ${envVersion || ""} can't answer ${t} — this scenario comes back not measured until the environment is rebuilt.`
          : notCalled
            ? `Agent ${agentLabel || ""} doesn't call ${t}. The scenario still runs — it measures that gap in the agent.`
            : "";
        return (
          <Tooltip key={t} arrow title={tip}>
            <Box
              sx={{
                px: 0.625, height: 20, display: "inline-flex", alignItems: "center", borderRadius: 0.5,
                bgcolor: notCalled ? "transparent" : "action.hover",
                border: notCalled ? "1px dashed" : "1px solid transparent",
                borderColor: notCalled ? "text.disabled" : "transparent",
                color: unanswered ? "#CA8A04" : notCalled ? "text.subtitle" : "text.secondary",
              }}
            >
              <Typography noWrap sx={{ typography: "s3", fontFamily: "ui-monospace, Menlo, monospace", fontSize: 11, color: "inherit" }}>{t}</Typography>
            </Box>
          </Tooltip>
        );
      })}
    </Stack>
  );
}
ToolChips.propTypes = {
  tools: PropTypes.array.isRequired,
  answers: PropTypes.object,
  agentCalls: PropTypes.object,
  agentLabel: PropTypes.string,
  envVersion: PropTypes.string,
};

export default function ScenarioTable({
  rows, groups, env, envVersion, onEdit, onRemove, onToggleBlocker, selectedIds, onSelectionChange, locked = false,
}) {
  const { statusOf, toolsOf, answers, agentCalls, agentLabel } = useContext(ScenarioRowContext);
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
    "select", "#", "Scenario", "Status", "Persona", "Situation",
    "Sub-goals", "Tools it needs", "Ideal outcome", "",
  ];
  let counter = 0;

  /*
    The table is wider than most panes, so it scrolls sideways. Two things
    keep that readable: the identity columns (select, #, Scenario) and the
    actions stay pinned, and an edge shadow appears only where something is
    actually scrolled out of view behind a pinned column.
  */
  const scrollRef = useRef(null);
  const [edge, setEdge] = useState({ left: false, right: false, width: 0 });
  const measure = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const next = {
      left: el.scrollLeft > 1,
      right: el.scrollLeft + el.clientWidth < el.scrollWidth - 1,
      width: el.clientWidth,
    };
    setEdge((prev) => (
      prev.left === next.left && prev.right === next.right && prev.width === next.width ? prev : next
    ));
  }, []);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return undefined;
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    el.addEventListener("scroll", measure, { passive: true });
    return () => { ro.disconnect(); el.removeEventListener("scroll", measure); };
  }, [measure]);

  const edgeShadow = (dir, on) => ({
    transition: "box-shadow 150ms",
    boxShadow: (t) => (on
      ? `${dir === "right" ? 8 : -8}px 0 12px -6px ${alpha(t.palette.common.black, t.palette.mode === "dark" ? 0.55 : 0.1)}`
      : "none"),
  });
  /* Pinned-left cells: opaque so columns slide under them, with the same
     hover tint as the rest of the row. The last one carries the shadow. */
  const pin = (i, head) => ({
    position: "sticky", left: PIN_LEFT[i], zIndex: head ? 3 : 1,
    bgcolor: "background.paper",
    boxSizing: "border-box", width: PIN_W[i], minWidth: PIN_W[i], maxWidth: PIN_W[i],
    ...(!head && {
      ".MuiTableRow-hover:hover &": {
        backgroundImage: (t) => `linear-gradient(${t.palette.action.hover}, ${t.palette.action.hover})`,
      },
    }),
    ...(i === PIN_W.length - 1 && edgeShadow("right", edge.left)),
  });


  return (
    <Box ref={scrollRef} sx={{ overflowX: "auto" }}>
      <Table size="small" sx={{ minWidth: 1640 }}>
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
              const pinned = i < PIN_W.length;
              return (
                <TableCell
                  key={h || i}
                  align={isActions ? "right" : "left"}
                  padding={isSelect ? "checkbox" : "normal"}
                  sx={{
                    typography: "s3", fontWeight: 700, color: "text.subtitle",
                    textTransform: "uppercase", letterSpacing: .4,
                    bgcolor: "background.paper",
                    borderBottom: "1px solid", borderColor: "divider",
                    whiteSpace: "nowrap",
                    ...(pinned && pin(i, true)),
                    ...(isSelect && { pl: 1.5 }),
                    ...(h === "#" && { px: 1 }),
                    ...(isActions && {
                      position: "sticky", right: 0, zIndex: 3,
                      width: ACTIONS_W, minWidth: ACTIONS_W,
                      ...edgeShadow("left", edge.right),
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
                    {/* Pinned to the visible width, so the group's name and
                        count stay in view while the columns scroll. */}
                    <Stack
                      direction="row" alignItems="center" spacing={1.25}
                      sx={{ position: "sticky", left: 16, width: edge.width ? edge.width - 32 : "auto" }}
                    >
                      <Iconify icon="solar:alt-arrow-down-linear" width={11} sx={{ color: "text.subtitle" }} />
                      <Typography sx={{ typography: "s2", fontWeight: 600, color: "text.primary", flex: 1, minWidth: 0, fontSize: 12.5 }}>
                        {section.label}
                      </Typography>
                      <Typography sx={{ typography: "s3", fontWeight: 600, color: "text.subtitle", fontVariantNumeric: "tabular-nums", fontSize: 11 }}>
                        {section.rows.length}
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
                  !p?.gender && !p?.ageGroup && p?.role, /* fall back to role for requesters */
                ].filter(Boolean).join(" · ");
                const situationText = row.situation || row.task || "";
                const idealOutcomeText = row.outcome || row.expected || "";

                return (
                  <TableRow key={row.id} hover>
                    <TableCell padding="checkbox" sx={{ ...pin(0), pl: 1.5, verticalAlign: "middle" }}>
                      <Checkbox
                        size="small"
                        checked={selected.has(row.id)}
                        onChange={() => toggle(row.id)}
                        disabled={locked}
                        sx={selectableCheckboxSx}
                      />
                    </TableCell>
                    <TableCell sx={{ ...pin(1), px: 1, typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums", verticalAlign: "middle" }}>
                      {idx}
                    </TableCell>

                {/* SCENARIO — name (bold, truncated) + summary (subtle,
                    truncated). Both wrapped in tooltips so long values
                    are readable on hover. */}
                <TableCell sx={{ ...pin(2), verticalAlign: "middle" }}>
                  <Stack direction="row" alignItems="center" spacing={0.75}>
                    <TruncTooltip title={row.name || row.title}>
                      <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{row.name || row.title}</Typography>
                    </TruncTooltip>
                    {row.newTool && (
                      <Tooltip
                        arrow
                        title={laterThanPinned(row, envVersion)
                          ? `Added when environment ${row.addedInEnv} learned ${row.newTool}. It isn't part of environment ${envVersion}, so runs on ${envVersion} leave it out.`
                          : `Added when environment ${row.addedInEnv} learned ${row.newTool}`}
                      >
                        <Box
                          sx={{
                            display: "inline-flex", alignItems: "center", flexShrink: 0,
                            height: 16, px: 0.5, borderRadius: 0.5,
                            border: "1px solid", borderColor: "divider", color: "text.secondary",
                          }}
                        >
                          <Typography sx={{ typography: "s3", fontWeight: 700 }}>
                            {laterThanPinned(row, envVersion) ? `Needs env ${row.addedInEnv}` : `New · env ${row.addedInEnv}`}
                          </Typography>
                        </Box>
                      </Tooltip>
                    )}
                    {row.provedBroke && (!row.brokeAgainst || !envVersion || row.brokeAgainst === envVersion) && (
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
                    <BlockerFlag row={row} onToggle={onToggleBlocker} locked={locked} />
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

                {/* STATUS — one answer to "can I trust this row?", with the
                    reason on hover. Same status the Needs attention chip
                    counts. */}
                <TableCell sx={{ verticalAlign: "middle", whiteSpace: "nowrap" }}>
                  {statusOf && <StatusChip status={statusOf(row)} />}
                </TableCell>

                {/* PERSONA — name + gender/age line */}
                <TableCell sx={{ maxWidth: 200, verticalAlign: "middle" }}>
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
                <TableCell sx={{ maxWidth: 320, verticalAlign: "middle" }}>
                  <ClampCell text={situationText} />
                </TableCell>

                {/* SUB-TASKS — moved up to sit next to the persona /
                    situation columns because it describes the *shape*
                    of the task, not its result. Same 3-row cap with a
                    hover popover for the full list. */}
                <TableCell sx={{ maxWidth: 260, verticalAlign: "middle" }}>
                  <SubTasksCell subTasks={subTasks} />
                </TableCell>

                {/* TOOLS IT NEEDS — the tools the world must answer for
                    this scenario; amber when the pinned world can't. */}
                <TableCell sx={{ maxWidth: 220, verticalAlign: "middle" }}>
                  <ToolChips
                    tools={toolsOf ? toolsOf(row) : (row.requiredTools || [])}
                    answers={answers}
                    agentCalls={agentCalls}
                    agentLabel={agentLabel}
                    envVersion={envVersion}
                  />
                </TableCell>

                {/* IDEAL OUTCOME — clamped to 3 lines, tooltip on
                    hover. Renamed from "Outcome" so it clearly
                    describes the criterion, not what a specific run
                    actually did. */}
                <TableCell sx={{ maxWidth: 320, verticalAlign: "middle" }}>
                  <ClampCell text={idealOutcomeText} />
                </TableCell>

                <TableCell
                  align="right"
                  sx={{
                    whiteSpace: "nowrap", verticalAlign: "middle",
                    position: "sticky", right: 0, zIndex: 1,
                    width: ACTIONS_W, minWidth: ACTIONS_W,
                    /*
                      Sticky cells need an opaque background to hide the
                      columns scrolling underneath. Layering the two on
                      backgroundImage keeps the row-hover tint visually
                      identical to the rest of the row: same
                      action.hover overlay, same paper base — no more
                      mismatched patch on hover.
                    */
                    bgcolor: "background.paper",
                    ...edgeShadow("left", edge.right),
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
  envVersion: PropTypes.string,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  onToggleBlocker: PropTypes.func,
  onHideGroup: PropTypes.func,
  selectedIds: PropTypes.array,
  onSelectionChange: PropTypes.func,
  locked: PropTypes.bool,
};

/* Pinned-left columns: select, #, Scenario — widths and offsets. */
const PIN_W = [48, 48, 300];
const PIN_LEFT = PIN_W.map((_, i) => PIN_W.slice(0, i).reduce((a, w) => a + w, 0));
const ACTIONS_W = 96;

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
