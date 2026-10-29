import PropTypes from "prop-types";
import { Fragment, useContext, useEffect, useMemo, useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  IconButton,
  Tooltip,
  Checkbox,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { subGoalsOfScenario } from "../../_mock/envModel";
import { admissionOf } from "../../_mock/coverage";
import { versionNumber } from "../../_mock/versions";
import { ScenarioRowContext } from "./scenarioRowContext";
import BlockerFlag from "./BlockerFlag";
import SourceBadge from "./SourceBadge";
import {
  StateChip,
  ArchivePopover,
  TextAction,
  ProposedBadge,
} from "./ScenarioStateBits";
import {
  stateActionOf,
  STATE_ACTION_LABEL,
  archivedOf,
} from "../../_mock/scenarioStates";
import { subGoalResultsOf } from "../../_mock/scenarioSubGoals";
import { envLabelOf } from "../../_mock/envVersionKinds";
import { clickable, focusRing } from "../../components/clickable";
import { scenarioTitle } from "../../_mock/scenarios";
import { asSentence, parseBrief } from "../../_mock/scenarioBrief";

/* Neutral white-on-selected checkbox — no primary colour, keeps the
   table's monochrome treatment. */
const selectableCheckboxSx = {
  p: 0,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

const PASS = "status.success.solid";
const FAIL = "status.danger.solid";
const FLAKY = "status.warning.solid";

/* Shown on row hover only — so thirty rows of icons don't shout. */

/**
 * Scenarios as a table — one calm line each.
 *
 * The row answers "which scenario is this, who calls, can I trust it, and how
 * has it been doing": its name, the caller, its state when it needs
 * attention, whether it's must-pass, and its last runs.
 * Everything else — the situation, sub-goals, tools, ideal outcome, proof —
 * is one click away in the scenario itself. Edit, archive and delete sit on
 * the row's hover.
 */
/* A scenario a later rebuild added is not part of an older pinned version. */
const laterThanPinned = (row, envVersion) =>
  !!(
    row?.addedInEnv &&
    envVersion &&
    versionNumber(row.addedInEnv) > versionNumber(envVersion)
  );

/* The identity columns stay put while the rest scrolls sideways. */
const PIN_LEFT = { select: 0, "#": 40, scenario: 80 };
const PINNED_SX = {
  "& td.scn-pin, & th.scn-pin": {
    position: "sticky",
    bgcolor: "background.paper",
    zIndex: 2,
  },
  "& td.scn-pin-0": { left: 0 },
  "& td.scn-pin-1": { left: 40 },
  "& td.scn-pin-2": { left: 80 },
  /* The row's hover and selection tint carry into the pinned cells. */
  "& tr:hover > td.scn-pin": {
    backgroundImage: (t) =>
      `linear-gradient(${t.palette.action.hover}, ${t.palette.action.hover})`,
  },
  "& tr.Mui-selected > td.scn-pin, & tr.scn-selected > td.scn-pin": {
    backgroundImage: (t) =>
      `linear-gradient(${t.palette.action.selected}, ${t.palette.action.selected})`,
  },
  "& .scn-pin-last": { boxShadow: (t) => `1px 0 0 ${t.palette.divider}` },
};

/* The brief's opening, without the "Your details: …" block the caller carries. */
const briefLineOf = (row) => {
  const text = row.task || row.situation || "";
  const b = parseBrief(text);
  return (b.intro || text).trim();
};

const COLUMNS = [
  { id: "select", w: 40 },
  { id: "#", label: "#", w: 40 },
  {
    id: "scenario",
    label: "Scenario",
    w: 380,
    tip: "A status shows only when the scenario needs something",
  },
  { id: "persona", label: "Persona", w: 170 },
  {
    id: "org",
    label: "Tags · groups",
    w: 180,
    tip: "Tags describe and filter; groups select scenarios for a run",
  },
  {
    id: "subgoals",
    label: "Sub-goals",
    w: 88,
    align: "right",
    tip: "Sub-goals met in the scenario's latest run, of all it's graded on",
  },
  {
    id: "mustPass",
    label: "Must-pass",
    w: 84,
    align: "center",
    tip: "An improvement that breaks a must-pass scenario is rejected",
  },
  {
    id: "runs",
    label: "Last runs",
    w: 116,
    tip: "This scenario in the environment's last runs, oldest to latest",
  },
  /* What the scenario is about — the same words as the drawer. After the
     status columns, so how it's doing stays in view in a narrow pane. */
  { id: "brief", label: "Brief", w: 340, tip: "What the caller is told to do" },
  {
    id: "passes",
    label: "Passes when",
    w: 260,
    tip: "What the agent has to do for the scenario to pass",
  },
  { id: "actions", w: 48 },
];

/*
  The state only when it asks for something — a column of "Valid" is noise.
  Its action is the first item in the row's ⋯ menu.
*/
function StatusCell({ state }) {
  if (!state || state.id === "valid") return null;
  return <StateChip state={state} />;
}
StatusCell.propTypes = { state: PropTypes.object };

/* Every row action in one ⋯ menu: the state's fix first, then edit,
   archive or restore, remove. */
function RowMenu({
  row,
  state,
  archived,
  locked,
  onEdit,
  onStateAction,
  onRemove,
}) {
  const [menuEl, setMenuEl] = useState(null);
  const [archiveEl, setArchiveEl] = useState(null);
  const fix = state && state.id !== "valid" ? stateActionOf(state) : null;
  const close = () => setMenuEl(null);
  const item = (key, icon, label, onClick, disabled) => (
    <MenuItem
      key={key}
      disabled={disabled}
      onClick={() => {
        close();
        onClick();
      }}
      sx={{ typography: "s2" }}
    >
      <ListItemIcon sx={{ minWidth: 28 }}>
        <Iconify icon={icon} width={16} />
      </ListItemIcon>
      <ListItemText
        primary={label}
        primaryTypographyProps={{ sx: { typography: "s2" } }}
      />
    </MenuItem>
  );
  return (
    <>
      <IconButton
        size="small"
        aria-label="Row actions"
        onClick={(e) => setMenuEl(e.currentTarget)}
        sx={{ color: "text.subtitle" }}
      >
        <Iconify icon="solar:menu-dots-bold" width={16} />
      </IconButton>
      <Menu anchorEl={menuEl} open={!!menuEl} onClose={close}>
        {fix &&
          onStateAction &&
          item(
            "fix",
            "solar:refresh-linear",
            STATE_ACTION_LABEL[fix],
            () => onStateAction(fix, row),
            locked,
          )}
        {item(
          "edit",
          "solar:pen-new-square-linear",
          "Edit scenario",
          () => onEdit(row),
          locked,
        )}
        {archived
          ? item(
              "restore",
              "solar:restart-linear",
              "Restore",
              () => onStateAction?.("restore", row),
              locked,
            )
          : item(
              "archive",
              "solar:archive-linear",
              "Archive…",
              () => setArchiveEl(menuEl),
              locked,
            )}
        {item(
          "remove",
          "solar:trash-bin-trash-linear",
          "Remove from environment",
          () => onRemove(row.id),
          locked,
        )}
      </Menu>
      <ArchivePopover
        anchorEl={archiveEl}
        onClose={() => setArchiveEl(null)}
        onArchive={(note) => onStateAction?.("archive", row, note)}
      />
    </>
  );
}
RowMenu.propTypes = {
  row: PropTypes.object.isRequired,
  state: PropTypes.object,
  archived: PropTypes.bool,
  locked: PropTypes.bool,
  onEdit: PropTypes.func,
  onStateAction: PropTypes.func,
  onRemove: PropTypes.func,
};

/* A proposed scenario waits for someone to add it — the review step. */
function ProposedTag({ row, onAction, locked }) {
  if (!row.proposed) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.75}
      sx={{ flexShrink: 0 }}
    >
      <ProposedBadge />
      {onAction && !locked && (
        <TextAction
          onClick={(e) => {
            e.stopPropagation();
            onAction("approve", row);
          }}
        >
          Add
        </TextAction>
      )}
    </Stack>
  );
}
ProposedTag.propTypes = {
  row: PropTypes.object.isRequired,
  onAction: PropTypes.func,
  locked: PropTypes.bool,
};

/* "2/3" — sub-goals met in the latest run the scenario was in, each one on
   hover. No run yet: just how many it has. */
function SubGoalsCell({ total, latest, env, envState }) {
  const results = latest
    ? subGoalResultsOf(latest.task, env, envState).items
    : [];
  /* Never run: just how many it's graded on — "–/3" read as empty. */
  if (!latest && total) {
    return (
      <Tooltip
        arrow
        title={`Graded on ${total} sub-goal${total === 1 ? "" : "s"} · how many were met shows after the first run`}
      >
        <Typography
          sx={{
            typography: "s2",
            color: "text.secondary",
            fontVariantNumeric: "tabular-nums",
            cursor: "default",
          }}
        >
          {total}
        </Typography>
      </Tooltip>
    );
  }
  if (!results.length) {
    return (
      <Tooltip
        arrow
        title={total ? `${total} sub-goals · not scored in a recent run` : ""}
      >
        <Typography
          sx={{
            typography: "s2",
            color: "text.tertiary",
            fontVariantNumeric: "tabular-nums",
            cursor: "default",
          }}
        >
          {total ? `–/${total}` : "—"}
        </Typography>
      </Tooltip>
    );
  }
  const met = results.filter((g) => g.status === "met").length;
  const title = (
    <Stack spacing={0.25} sx={{ py: 0.25 }}>
      <Typography
        sx={{ typography: "s3", fontWeight: 600, color: "inherit" }}
      >{`${latest.name} · ${met} of ${results.length} met`}</Typography>
      {results.map((g) => (
        <Typography
          key={g.key || g.text}
          sx={{ typography: "s3", color: "inherit" }}
        >
          {`${g.status === "met" ? "✓" : g.status === "inconclusive" ? "?" : "✗"} ${g.text || g.label}`}
        </Typography>
      ))}
    </Stack>
  );
  return (
    <Tooltip arrow title={title}>
      <Typography
        sx={{
          typography: "s2",
          fontVariantNumeric: "tabular-nums",
          cursor: "default",
          color: "text.secondary",
        }}
      >
        <Box
          component="span"
          sx={{
            color: met < results.length ? "text.primary" : "text.secondary",
            fontWeight: met < results.length ? 600 : 400,
          }}
        >
          {met}
        </Box>
        <Box
          component="span"
          sx={{ color: "text.tertiary" }}
        >{`/${results.length}`}</Box>
      </Typography>
    </Tooltip>
  );
}
SubGoalsCell.propTypes = {
  total: PropTypes.number,
  latest: PropTypes.object,
  env: PropTypes.object,
  envState: PropTypes.object,
};

/* The last runs as dots, oldest to latest: green passed, red failed, orange
   flaky, hollow when the scenario wasn't in that run or wasn't scored. */
function RunDots({ history }) {
  if (!history?.length)
    return (
      <Typography sx={{ typography: "s3", color: "text.disabled" }}>
        No runs yet
      </Typography>
    );
  return (
    <Stack direction="row" spacing={0.5} alignItems="center">
      {history.map((h) => {
        const color =
          { passed: PASS, failed: FAIL, flaky: FLAKY }[h.status] || null;
        const word =
          { passed: "Passed", failed: "Failed", flaky: "Flaky" }[h.status] ||
          (h.status ? "Not scored" : "Not in this run");
        return (
          <Tooltip key={h.runId} arrow title={`${h.name} · ${word}`}>
            <Box
              sx={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                flexShrink: 0,
                bgcolor: color || "transparent",
                border: color ? "none" : "1px solid",
                borderColor: "text.disabled",
              }}
            />
          </Tooltip>
        );
      })}
    </Stack>
  );
}
RunDots.propTypes = { history: PropTypes.array };

export default function ScenarioTable({
  rows,
  groups,
  env,
  envState,
  envVersion,
  onEdit,
  onRemove,
  selectedIds,
  onSelectionChange,
  locked = false,
}) {
  /* Stored env labels read as major.minor ("v3" → "v2.1"). */
  const vl = (label) => (envState ? envLabelOf(env, envState, label) : label);
  const { stateOf, onStateAction, onOpen, historyOf, latestOf } =
    useContext(ScenarioRowContext);
  /*
    Two shapes come in: pre-grouped (list view mirror) or a flat rows
    array (fallback). If groups are given, render section-header rows
    between them so the table reads the same use-case story as the
    list; if not, fall back to a flat table.
  */
  const sections = useMemo(
    () =>
      groups?.length ? groups : [{ id: "all", label: null, rows: rows || [] }],
    [groups, rows],
  );

  const allIds = useMemo(
    () =>
      sections.flatMap((s) => (s.rows || []).map((r) => r.id)).filter(Boolean),
    [sections],
  );

  /*
    Selection is a bulk-action affordance, not a run gate — every scenario in
    the table runs on the next simulation regardless. Controlled from the
    parent so the SelectionBar and the workspace chat stay in sync; falls
    back to internal state when unwired.
  */
  const [internalSelected, setInternalSelected] = useState(() => new Set());
  const isControlled = Array.isArray(selectedIds);
  const selected = isControlled ? new Set(selectedIds) : internalSelected;

  const commit = (next) => {
    if (!isControlled) setInternalSelected(next);
    onSelectionChange?.(Array.from(next));
  };

  /* Uncontrolled only: drop ids for rows that are gone. Controlled tables
     (one per batch, sharing one selection) leave pruning to the parent. */
  useEffect(() => {
    if (isControlled) return;
    const cleaned = Array.from(selected).filter((id) => allIds.includes(id));
    if (cleaned.length !== selected.size) commit(new Set(cleaned));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allIds.join("|")]);

  const toggle = (id) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    commit(next);
  };
  /* Select-all affects only this batch's rows. */
  const hereSelected = allIds.filter((id) => selected.has(id)).length;
  const toggleAll = () => {
    const next = new Set(selected);
    if (hereSelected === allIds.length && allIds.length > 0)
      allIds.forEach((id) => next.delete(id));
    else allIds.forEach((id) => next.add(id));
    commit(next);
  };
  const allChecked = allIds.length > 0 && hereSelected === allIds.length;
  const someChecked = hereSelected > 0 && hereSelected < allIds.length;
  let counter = 0;

  const cellSx = {
    py: 0,
    height: 48,
    verticalAlign: "middle",
    borderColor: "divider",
  };
  /* The runs column says how many runs its dots cover — the environment's last
     eight, or fewer while it has fewer. */
  const firstRow =
    (rows && rows[0]) || groups?.find((g) => g.rows?.length)?.rows[0];
  const runsShown =
    historyOf && firstRow ? (historyOf(firstRow) || []).length : 0;
  /* No runs yet: the runs column would be "No runs yet" on every row — leave it out. */
  const columns = runsShown ? COLUMNS : COLUMNS.filter((c) => c.id !== "runs");
  const labelOf = (c) =>
    c.id === "runs" && runsShown
      ? `Last ${runsShown} run${runsShown === 1 ? "" : "s"}`
      : c.label;

  return (
    <Box sx={{ overflowX: "auto" }}>
      {/* Wider than most panes, so it scrolls sideways; the checkbox, #
          and Scenario stay pinned so a row never loses its name. */}
      <Table
        size="small"
        sx={{ minWidth: 1580, tableLayout: "fixed", ...PINNED_SX }}
      >
        <colgroup>
          {columns.map((c) => (
            <col key={c.id} style={c.w ? { width: c.w } : undefined} />
          ))}
        </colgroup>
        <TableHead>
          <TableRow>
            {columns.map((c) => (
              <TableCell
                key={c.id}
                align={c.align || (c.id === "actions" ? "right" : "left")}
                padding={c.id === "select" ? "checkbox" : "normal"}
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.tertiary",
                  whiteSpace: "nowrap",
                  bgcolor: "background.paper",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  py: 1,
                  ...(c.id === "select" && { pl: 1.5 }),
                  ...(c.id === "#" && { px: 1 }),
                  ...(PIN_LEFT[c.id] != null && {
                    position: "sticky",
                    left: PIN_LEFT[c.id],
                    zIndex: 3,
                  }),
                }}
                className={
                  PIN_LEFT[c.id] != null
                    ? `scn-pin${c.id === "scenario" ? " scn-pin-last" : ""}`
                    : undefined
                }
              >
                {c.id === "select" ? (
                  <Checkbox
                    size="small"
                    checked={allChecked}
                    indeterminate={someChecked}
                    onChange={toggleAll}
                    disabled={locked}
                    sx={selectableCheckboxSx}
                  />
                ) : c.tip ? (
                  <Tooltip arrow title={c.tip}>
                    <span>{labelOf(c)}</span>
                  </Tooltip>
                ) : (
                  labelOf(c)
                )}
              </TableCell>
            ))}
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
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      py: 1,
                      px: 2,
                    }}
                  >
                    {/* Pinned like the identity columns, so a heading never scrolls out of view. */}
                    <Stack
                      direction="row"
                      alignItems="center"
                      spacing={1}
                      sx={{
                        position: "sticky",
                        left: 16,
                        width: "fit-content",
                        maxWidth: 960,
                      }}
                    >
                      <Typography
                        noWrap
                        sx={{ typography: "s2", fontWeight: 600, minWidth: 0 }}
                      >
                        {section.label}
                      </Typography>
                      <Typography
                        sx={{
                          typography: "s3",
                          fontWeight: 600,
                          color: "text.tertiary",
                          fontVariantNumeric: "tabular-nums",
                          flexShrink: 0,
                        }}
                      >
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
                const archived = !!archivedOf(row);
                /* The sub-goals it's graded on — a stored list (a generated or edited
                   scenario) wins, the same list a run scores, so the count before a
                   run is the denominator after it. */
                const goals =
                  (row.subTasks?.length
                    ? row.subTasks
                    : subGoalsOfScenario(row, env, envState)) || [];
                const headline =
                  row.summary || scenarioTitle(row.title) || row.name;
                const adm = admissionOf(row, env);
                const personaMeta = [p?.ageGroup, !p?.ageGroup && p?.role]
                  .filter(Boolean)
                  .join(" · ");
                const open = onOpen ? () => onOpen(row) : undefined;
                const stop = (e) => e.stopPropagation();
                return (
                  <TableRow
                    key={row.id}
                    hover
                    className={
                      selected.has(row.id) ? "scn-selected" : undefined
                    }
                    {...clickable(open, { role: "row" })}
                    sx={{
                      ...focusRing,
                      cursor: open ? "pointer" : "default",
                      ...(selected.has(row.id) && {
                        bgcolor: "action.selected",
                      }),
                      /* Archived rows stay listed, dimmed; the state and the actions keep full strength. */
                      /* On the cell's content, so a pinned cell keeps its solid background. */
                      ...(archived && { "& .scn-dim > *": { opacity: 0.45 } }),
                    }}
                  >
                    <TableCell
                      padding="checkbox"
                      className="scn-pin scn-pin-0"
                      sx={{ ...cellSx, pl: 1.5 }}
                      onClick={stop}
                    >
                      <Checkbox
                        size="small"
                        checked={selected.has(row.id)}
                        onChange={() => toggle(row.id)}
                        disabled={locked}
                        sx={selectableCheckboxSx}
                      />
                    </TableCell>
                    <TableCell
                      className="scn-pin scn-pin-1"
                      sx={{
                        ...cellSx,
                        px: 1,
                        typography: "s3",
                        color: "text.tertiary",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {idx}
                    </TableCell>

                    {/* SCENARIO — what it is, in words, and how many sub-goals
                        grade it. The id-style name is on hover and inside. */}
                    <TableCell
                      className="scn-dim scn-pin scn-pin-2 scn-pin-last"
                      sx={cellSx}
                    >
                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={0.75}
                        sx={{ minWidth: 0 }}
                      >
                        <Tooltip
                          arrow
                          enterDelay={400}
                          title={
                            row.name && row.name !== headline ? row.name : ""
                          }
                        >
                          <Typography
                            noWrap
                            sx={{
                              typography: "s2",
                              fontWeight: 500,
                              minWidth: 120,
                              flexShrink: 1,
                            }}
                          >
                            {headline}
                          </Typography>
                        </Tooltip>
                        {row.newTool && (
                          <Tooltip
                            arrow
                            title={
                              laterThanPinned(row, envVersion)
                                ? `Added when environment ${vl(row.addedInEnv)} learned ${row.newTool}. It isn't part of environment ${vl(envVersion)}, so runs on ${vl(envVersion)} leave it out.`
                                : `Added when environment ${vl(row.addedInEnv)} learned ${row.newTool}`
                            }
                          >
                            <Typography
                              noWrap
                              sx={{
                                typography: "s3",
                                color: "text.secondary",
                                flexShrink: 0,
                                px: 0.5,
                                border: "1px solid",
                                borderColor: "divider",
                                borderRadius: "4px",
                              }}
                            >
                              {laterThanPinned(row, envVersion)
                                ? `Needs environment ${vl(row.addedInEnv)}`
                                : "New"}
                            </Typography>
                          </Tooltip>
                        )}
                        {/* Where it came from — links back when there is somewhere to go. */}
                        <SourceBadge
                          scenario={row}
                          env={env}
                          envState={envState}
                        />
                        <ProposedTag
                          row={row}
                          onAction={onStateAction}
                          locked={locked}
                        />
                        {!adm.admitted && (
                          <Tooltip arrow title={`Set aside — ${adm.reason}`}>
                            <Box sx={{ display: "flex", flexShrink: 0 }}>
                              <Iconify
                                icon="solar:shield-cross-bold"
                                width={14}
                                sx={{ color: "status.danger.fg" }}
                              />
                            </Box>
                          </Tooltip>
                        )}
                        {stateOf && (
                          <Box
                            onClick={stop}
                            sx={{ display: "inline-flex", flexShrink: 0 }}
                          >
                            <StatusCell state={stateOf(row)} />
                          </Box>
                        )}
                      </Stack>
                    </TableCell>

                    {/* PERSONA — who calls, in one line. */}
                    <TableCell className="scn-dim" sx={cellSx}>
                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={0.5}
                        sx={{ minWidth: 0 }}
                      >
                        {p?.libraryId != null && (
                          <Tooltip
                            arrow
                            title={
                              p.edited
                                ? "From the persona library, edited here"
                                : "From the persona library"
                            }
                          >
                            <Box sx={{ display: "flex", flexShrink: 0 }}>
                              <Iconify
                                icon="solar:users-group-rounded-linear"
                                width={14}
                                sx={{ color: "text.tertiary" }}
                              />
                            </Box>
                          </Tooltip>
                        )}
                        <Typography
                          noWrap
                          sx={{
                            typography: "s2",
                            color: "text.secondary",
                            minWidth: 0,
                          }}
                        >
                          {p?.name || "—"}
                          {personaMeta && (
                            <Box
                              component="span"
                              sx={{ color: "text.tertiary" }}
                            >{` · ${personaMeta}`}</Box>
                          )}
                        </Typography>
                      </Stack>
                    </TableCell>

                    {/* TAGS · GROUPS — one line; the drawer edits them. */}
                    <TableCell sx={cellSx}>
                      <OneLine
                        text={[
                          ...(envState?.scenarioTags || [])
                            .filter((t) => (row.tagIds || []).includes(t.id))
                            .map((t) => t.name),
                          ...(envState?.scenarioGroups || [])
                            .filter((g) => g.ids.includes(row.id))
                            .map((g) => `▸ ${g.name}`),
                        ].join(" · ")}
                      />
                    </TableCell>

                    {/* SUB-GOALS — met in the latest run, of all. */}
                    <TableCell align="right" sx={cellSx}>
                      <SubGoalsCell
                        total={goals.length}
                        latest={latestOf ? latestOf(row) : null}
                        env={env}
                        envState={envState}
                      />
                    </TableCell>

                    {/* MUST-PASS — the triangle, toggled in place; hollow on hover. */}
                    <TableCell align="center" sx={cellSx} onClick={stop}>
                      <BlockerFlag
                        row={row}
                        size={14}
                        locked={locked || !!row.archived}
                        onToggle={
                          onStateAction
                            ? () => onStateAction("blocker", row, !row.critical)
                            : undefined
                        }
                      />
                    </TableCell>

                    {/* LAST RUNS — how it's been doing (once there are runs). */}
                    {runsShown > 0 && (
                      <TableCell sx={cellSx}>
                        <RunDots history={historyOf ? historyOf(row) : null} />
                      </TableCell>
                    )}

                    {/* BRIEF and PASSES WHEN — one line each, the whole text on hover. */}
                    <TableCell className="scn-dim" sx={cellSx}>
                      <OneLine text={briefLineOf(row)} />
                    </TableCell>
                    <TableCell className="scn-dim" sx={cellSx}>
                      <OneLine
                        text={
                          row.expected || row.outcome
                            ? asSentence(row.expected || row.outcome)
                            : ""
                        }
                      />
                    </TableCell>

                    {/* Every row action, in the ⋯ menu. */}
                    <TableCell
                      align="right"
                      sx={{ ...cellSx, whiteSpace: "nowrap", pr: 1 }}
                      onClick={stop}
                    >
                      <RowMenu
                        row={row}
                        state={stateOf ? stateOf(row) : null}
                        archived={archived}
                        locked={locked}
                        onEdit={onEdit}
                        onStateAction={onStateAction}
                        onRemove={onRemove}
                      />
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
  rowHeight: PropTypes.string,
  groups: PropTypes.array,
  env: PropTypes.object,
  envState: PropTypes.object,
  envVersion: PropTypes.string,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  onHideGroup: PropTypes.func,
  selectedIds: PropTypes.array,
  onSelectionChange: PropTypes.func,
  locked: PropTypes.bool,
};

/* A cell's text on one line; the whole of it on hover. */
function OneLine({ text }) {
  if (!text)
    return (
      <Typography sx={{ typography: "s2", color: "text.disabled" }}>
        —
      </Typography>
    );
  return (
    <Tooltip
      arrow
      enterDelay={400}
      title={<Box sx={{ maxWidth: 420 }}>{text}</Box>}
    >
      <Typography
        noWrap
        sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
      >
        {text}
      </Typography>
    </Tooltip>
  );
}
OneLine.propTypes = { text: PropTypes.string };
