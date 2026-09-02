import PropTypes from "prop-types";
import { Fragment } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  IconButton, Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { subTasksFor } from "../../_mock/contract";

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
export default function ScenarioTable({ rows, groups, env, onEdit, onRemove }) {
  /*
    Two shapes come in: pre-grouped (list view mirror) or a flat rows
    array (fallback). If groups are given, render section-header rows
    between them so the table reads the same use-case story as the
    list; if not, fall back to a flat table.
  */
  const sections = groups?.length
    ? groups
    : [{ id: "all", label: null, rows: rows || [] }];

  /*
    Column order (final): scenario, persona, situation, sub-tasks,
    branch category, ideal outcome. "Conversation branch" was
    dropped — it duplicated what "Branch category" already carries at
    a scannable level, and it was the widest, monospace-heaviest
    column on the table. "Outcome" is renamed "Ideal outcome" so
    it's obvious the column describes what a good result looks like,
    not a run's actual result.
  */
  const columns = [
    "#", "Scenario", "Persona", "Situation",
    "Sub-tasks", "Branch category", "Ideal outcome", "",
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
              return (
                <TableCell
                  key={h || i}
                  align={isActions ? "right" : "left"}
                  sx={{
                    typography: "s3", fontWeight: 700, color: "text.subtitle",
                    textTransform: "uppercase", letterSpacing: .4,
                    /* Neutral fill restored on the column header row —
                       without it the header floated into the body and
                       the table lost its top edge. Group-header rows
                       below keep the same fill so the two rows read as
                       "the frame" and the body rows sit inside it. */
                    bgcolor: "background.neutral",
                    borderBottom: "1px solid", borderColor: "divider",
                    whiteSpace: "nowrap",
                    ...(h === "#" && { width: 44 }),
                    ...(isActions && {
                      position: "sticky", right: 0, zIndex: 2,
                      width: 96, minWidth: 96,
                      boxShadow: (t) => `-8px 0 12px -6px ${alpha(t.palette.common.black, t.palette.mode === "dark" ? 0.45 : 0.08)}`,
                    }),
                  }}
                >
                  {h}
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

                  {/*
                    Group header spans the whole table — sticky under
                    the column headers. Fill is a step stronger than
                    the column-header neutral (a subtle text-primary
                    tint over the paper base) so the two header rows
                    are clearly distinguishable at a glance: column
                    headers frame the columns, group headers frame the
                    use-case sections.
                  */}
                  <TableCell
                    colSpan={columns.length}
                    sx={{
                      position: "sticky", top: 0, zIndex: 1,
                      bgcolor: (t) => alpha(
                        t.palette.text.primary,
                        t.palette.mode === "dark" ? 0.08 : 0.05,
                      ),
                      borderTop: "1px solid", borderBottom: "1px solid", borderColor: "divider",
                      py: 1.125, px: 2,
                    }}
                  >
                    <Stack direction="row" alignItems="center" spacing={1.25}>
                      <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.primary", flex: 1, minWidth: 0 }}>
                        {section.label}
                      </Typography>
                      <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
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
                  !p?.gender && !p?.ageGroup && p?.role, /* fall back to role for requesters */
                ].filter(Boolean).join(" · ");
                const situationText = row.situation || row.task || "";
                const idealOutcomeText = row.outcome || row.expected || "";

                return (
                  <TableRow key={row.id} hover>
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
                    {row.critical && (
                      <Tooltip arrow title="Critical — a failure here is a release blocker">
                        <Box sx={{ display: "flex" }}>
                          <Iconify icon="solar:danger-triangle-bold" width={13} sx={{ color: "#DC2626" }} />
                        </Box>
                      </Tooltip>
                    )}
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

                {/* BRANCH CATEGORY — single-line label. */}
                <TableCell sx={{ maxWidth: 200, verticalAlign: "top" }}>
                  <TruncTooltip title={row.branchCategory || ""}>
                    <Typography noWrap sx={{ typography: "s2", color: "text.secondary" }}>
                      {row.branchCategory || "—"}
                    </Typography>
                  </TruncTooltip>
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
                  <Tooltip arrow title="Edit scenario">
                    <IconButton size="small" onClick={() => onEdit(row)}>
                      <Iconify icon="solar:pen-new-square-linear" width={15} sx={{ color: "text.subtitle" }} />
                    </IconButton>
                  </Tooltip>
                  <Tooltip arrow title="Remove from this environment">
                    <IconButton size="small" onClick={() => onRemove(row.id)}>
                      <Iconify icon="solar:close-circle-linear" width={15} sx={{ color: "text.subtitle" }} />
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
  rows: PropTypes.array.isRequired,
  groups: PropTypes.array,
  env: PropTypes.object,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
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
function SubTasksCell({ subTasks }) {
  const list = subTasks || [];
  if (!list.length) {
    return <Typography sx={{ typography: "s3", color: "text.subtitle" }}>—</Typography>;
  }
  const fullList = list.map((st, i) => `${i + 1}. ${st.label}`).join("\n");
  return (
    <TruncTooltip title={fullList}>
      <Stack spacing={0.375}>
        {list.slice(0, 3).map((st, i) => (
          <Stack key={st.id || i} direction="row" spacing={0.75} alignItems="flex-start">
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
