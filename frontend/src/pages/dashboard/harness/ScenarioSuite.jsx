import {
  Box,
  Button,
  Checkbox,
  Chip,
  Drawer,
  IconButton,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import { useSnackbar } from "notistack";

import { DataTablePagination } from "src/components/data-table";
import FilterPanel from "src/components/filter-panel/FilterPanel";
import Iconify from "src/components/iconify";
import {
  useAmendScenarios,
  useHarnessScenarioCoverage,
  useHarnessScenarios,
} from "src/api/harness/scenarios";
import { noiseValue } from "src/sections/simulate/environments/workspace/scenarios/scenarioEditor.constants";
import ScenarioEditForm from "./ScenarioEditForm";

const selectableCheckboxSx = {
  p: 0,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

// The design greys these controls out and says "Fork this environment to edit." We have no fork,
// so the reason has to be the one that is actually true here: a suite read outside a run has no
// job to amend against.
const lockedReason = "Open this suite from its run to edit it";

const COLUMNS = [
  "select",
  "#",
  "Scenario",
  "Persona",
  "Levers",
  "Situation",
  "Sub-goals",
  "Passes when",
  "",
];

// How the sections are cut. The server tags each row with the group it fell into, so this only
// names the choices; it never decides which rows belong to which.
const readable = (name) =>
  String(name || "")
    .replace(/[_-]+/g, " ")
    .trim();

// The panel answers in two shapes: the Basic tab returns `{field: [values]}` already carrying the
// `_not` suffix for a negation, the Query tab returns tokens. Both collapse to the same query
// params, which is all this component does with a filter: pass it on.
const toQueryParams = (result) => {
  if (!result) return {};
  if (!Array.isArray(result)) return result;
  const flat = {};
  result.forEach((token) => {
    const held = Array.isArray(token.value)
      ? token.value
      : [token.value].filter(Boolean);
    if (!held.length) return;
    const negated =
      token.operator === "is_not" || token.operator === "not_equals";
    const key = negated ? `${token.field}_not` : token.field;
    flat[key] = [...(flat[key] || []), ...held];
  });
  return flat;
};

// The harness answers each change separately, so the summary counts outcomes rather than claiming
// a single verdict for the batch. A rework that touched files is worth saying out loud.
const summarise = (receipts) => {
  const counts = receipts.reduce(
    (totals, one) => ({
      ...totals,
      [one.outcome]: (totals[one.outcome] || 0) + 1,
    }),
    {},
  );
  const parts = [];
  if (counts.applied) parts.push(`${counts.applied} applied`);
  if (counts.reworked) parts.push(`${counts.reworked} reworked`);
  if (counts.queued) parts.push(`${counts.queued} being re-checked`);
  if (counts.refused) parts.push(`${counts.refused} refused`);
  return parts.join(", ") || "nothing changed";
};

/**
 * The Scenarios tab. It displays a page and nothing else: the search, the filters, the grouping,
 * the ordering and the paging are all query params, and the server answers with the rows, the
 * filter panel's own field catalogue and the coverage grid.
 *
 * `scenarios` is only the suite read outside a run, where there is no job to page against.
 */
export default function ScenarioSuite({
  scenarios,
  jobId,
  editable,
  scenarioEditing,
  onChanged,
}) {
  const { enqueueSnackbar } = useSnackbar();
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [typed, setTyped] = useState("");
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState({});
  // Unset until the server says what it offers; the first grouping it names is the default.
  const [groupBy, setGroupBy] = useState(null);
  // Empty until the reader picks one. The server answers with the pair it used, and those are
  // what the two dropdowns show, so no axis name is written into this file.
  const [rowAxis, setRowAxis] = useState("");
  const [colAxis, setColAxis] = useState("");
  const [filterAnchor, setFilterAnchor] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [editing, setEditing] = useState(null);
  const [waiting, setWaiting] = useState(false);

  // One request per settled keystroke rather than one per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(typed);
      setPage(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [typed]);

  // One call carries the page, the field catalogue, the coverage grid and the group tags, so the
  // table, the panel and the sections all describe the same narrowed suite rather than three
  // different ones. It polls, so a suite still being written fills in while it is watched.
  const { data: served, isFetching } = useHarnessScenarios(jobId, {
    page,
    pageSize,
    search,
    groupBy: groupBy ?? undefined,
    filters,
    refetchInterval: waiting ? 6000 : 15000,
  });
  // Its own call, on its own route: the grid answers a different question from the list and does
  // not change when the reader pages or regroups.
  const { data: coverage } = useHarnessScenarioCoverage(jobId, {
    search,
    filters,
    rowAxis,
    colAxis,
  });
  const amend = useAmendScenarios(jobId);

  // Without a job there is no endpoint, so the prop is the whole suite and there is nothing to
  // filter it by. With one, the page the server sent is the answer.
  const rows = useMemo(
    () => (jobId ? served?.rows || [] : scenarios),
    [jobId, served, scenarios],
  );
  const total = jobId ? served?.total ?? 0 : scenarios.length;
  const fields = served?.fields || [];
  // The groupings the server offers. Nothing here is written into the client.
  const groupings = served?.groupings || [];
  const contract = served?.editing || scenarioEditing;
  const editableFields = contract?.editable_fields;
  const personaFields = contract?.persona_fields;
  const levelLabels = served?.levelLabels || {};
  const noiseChoices =
    fields.find((one) => one.value === "background_noise")?.choices || [];
  const filterFields = fields.map((one) =>
    one.value === "background_noise"
      ? { ...one, choiceLabels: levelLabels }
      : one,
  );

  const activeFilters = Object.keys(filters).length;
  const applyFilters = (result) => {
    setFilters(toQueryParams(result));
    setPage(0);
  };

  const allSelected =
    rows.length > 0 && rows.every((one) => selected.has(one.name));
  const someSelected =
    !allSelected && rows.some((one) => selected.has(one.name));
  const toggleAll = () =>
    setSelected((prev) => {
      const next = new Set(prev);
      rows.forEach((one) =>
        allSelected ? next.delete(one.name) : next.add(one.name),
      );
      return next;
    });

  // The server tags each row, orders the page by that tag and counts the sections. Drawing them
  // is a walk down rows that are already in the right order, never a regrouping: a page is one
  // page of a much larger suite, so anything gathered here would describe the page and not the
  // suite. No sections means no grouping was asked for, and the page draws as one run.
  const totals = useMemo(
    () => new Map((served?.groups || []).map((one) => [one.name, one])),
    [served],
  );

  // A queued change is still running in the harness, so the poll tightens until the suite itself
  // comes back different. The receipt is a promise; the suite is the answer.
  const fingerprint = rows
    .map((one) => `${one.name}:${one.sub_goals?.length ?? 0}`)
    .join("|");
  const seenRef = React.useRef(fingerprint);
  useEffect(() => {
    if (!waiting) {
      seenRef.current = fingerprint;
      return;
    }
    if (fingerprint !== seenRef.current) {
      setWaiting(false);
      enqueueSnackbar("The suite has been re-checked", { variant: "success" });
    }
  }, [waiting, fingerprint, enqueueSnackbar]);

  const toggle = (name) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  const toggleGroup = (inIt, allOn) =>
    setSelected((prev) => {
      const next = new Set(prev);
      inIt.forEach((row) =>
        allOn ? next.delete(row.name) : next.add(row.name),
      );
      return next;
    });

  const send = async (changes, { rework }) => {
    try {
      const reply = await amend.mutateAsync({ changes, rework });
      const receipts = reply?.receipts || [];
      const refused = receipts.filter((one) => one.outcome === "refused");
      const queued = receipts.filter((one) => one.outcome === "queued");
      enqueueSnackbar(summarise(receipts), {
        variant: refused.length ? "warning" : "success",
      });
      if (queued.length) setWaiting(true);
      // A refusal carries the reason the harness gave. Showing it is the difference between "that
      // did not work" and knowing which change to send differently.
      refused.forEach((one) =>
        enqueueSnackbar(`${readable(one.scenario)}: ${one.why}`, {
          variant: "warning",
        }),
      );
      setSelected(new Set());
      setEditing(null);
      onChanged?.();
    } catch (error) {
      // The harness names the offending op or field; anything else is ours to keep off screen.
      const detail = error?.response?.data?.detail;
      enqueueSnackbar(detail || "The suite could not be edited", {
        variant: "error",
      });
    }
  };

  const busy = amend.isPending;
  const dropOne = (name) =>
    send([{ op: "drop", scenario: name }], { rework: false });
  // One change naming every scenario, not one change each: the harness expands it and answers
  // with a receipt per scenario either way, and a single change is what the route is shaped for.
  const deleteSelected = () =>
    send([{ op: "drop", scenarios: [...selected] }], { rework: false });

  // One edit, two kinds of change. The descriptive fields are written straight to the scenario;
  // the persona may still turn out to matter for this agent, and the harness decides that, not us.
  const saveScenario = (form, before = {}) => {
    // One scenario at a time. Each scenario's checks are its own, so there is nothing sensible
    // to apply across a selection: a shared "passes when" would erase what makes each a test.
    const naming = { scenario: editing.name };
    const offered = (field) => (editableFields || []).includes(field);
    const changed = (key) => JSON.stringify(form[key]) !== JSON.stringify(before[key]);
    const changes = [
      changed("tests") && { op: "set_field", ...naming, field: "tests", value: form.tests },
      changed("keywords") && { op: "set_field", ...naming, field: "keywords", value: form.keywords },
      offered("max_turns") &&
        changed("max_turns") &&
        form.max_turns != null && {
          op: "set_field",
          ...naming,
          field: "max_turns",
          value: form.max_turns,
        },
      offered("background_noise") &&
        changed("background_noise") && {
          op: "set_field",
          ...naming,
          field: "background_noise",
          value: noiseValue(form.background_noise),
        },
    ].filter(Boolean);
    if (editing.persona) {
      const persona = Object.fromEntries(
        [
          "personality",
          "communication_style",
          "accent",
          "languages",
          "occupation",
          "location",
        ]
          .filter((key) => !personaFields || personaFields.includes(key))
          .filter(changed)
          .map((key) => [key, form[key]]),
      );
      if (Object.keys(persona).length) {
        changes.push({ op: "set_persona", ...naming, persona });
      }
    }
    if (!changes.length) {
      setEditing(null);
      return;
    }
    send(changes, { rework: true });
  };

  if (!jobId && !scenarios.length) {
    return (
      <Typography variant="body2" color="text.secondary">
        No scenarios yet.
      </Typography>
    );
  }

  return (
    <Stack spacing={1.5} sx={{ minWidth: 0 }}>
      {waiting && (
        <Typography variant="caption" color="text.secondary">
          The harness is re-checking a scenario, rewriting its setup and checks
          where the change matters and proving it again. This takes a minute or
          two.
        </Typography>
      )}

      {/* The grid reads the whole suite, so it sits above the page it describes rather than
          under it. Its two axes are its own filter and do not narrow the table. */}
      {coverage && (
        <CoverageGrid
          coverage={coverage}
          rowAxis={rowAxis || coverage.row_axis}
          colAxis={colAxis || coverage.col_axis}
          onRowAxis={setRowAxis}
          onColAxis={setColAxis}
        />
      )}

      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        flexWrap="wrap"
        useFlexGap
      >
        <TextField
          size="small"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          placeholder="Search scenarios by name, task or use case"
          InputProps={{
            sx: { typography: "s2" },
            startAdornment: (
              <Box
                sx={{
                  pr: 0.75,
                  pl: 0.25,
                  display: "flex",
                  color: "text.subtitle",
                }}
              >
                <Iconify icon="solar:magnifer-linear" width={14} />
              </Box>
            ),
          }}
          sx={{ maxWidth: 380, flex: 1 }}
        />
        {jobId && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={(event) => setFilterAnchor(event.currentTarget)}
            startIcon={<Iconify icon="solar:filter-linear" width={16} />}
          >
            Filter{activeFilters ? ` (${activeFilters})` : ""}
          </Button>
        )}
        {jobId && (
          <TextField
            select
            size="small"
            label="Group by"
            value={groupBy ?? served?.groupBy ?? ""}
            onChange={(event) => {
              setGroupBy(event.target.value);
              setPage(0);
            }}
            sx={{ minWidth: 150 }}
          >
            {groupings.map((one) => (
              <MenuItem key={one.value || "none"} value={one.value}>
                {one.label}
              </MenuItem>
            ))}
          </TextField>
        )}
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            fontVariantNumeric: "tabular-nums",
            whiteSpace: "nowrap",
          }}
        >
          {total} {total === 1 ? "scenario" : "scenarios"}
          {isFetching ? " · refreshing" : ""}
        </Typography>
      </Stack>

      {selected.size > 0 && (
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="body2">{selected.size} selected</Typography>
          <Button
            size="small"
            color="error"
            variant="outlined"
            disabled={busy}
            startIcon={
              <Iconify icon="solar:trash-bin-trash-linear" width={16} />
            }
            onClick={deleteSelected}
          >
            Delete
          </Button>
          <Button
            size="small"
            disabled={busy}
            onClick={() => setSelected(new Set())}
          >
            Clear
          </Button>
        </Stack>
      )}

      <TableContainer
        sx={{ width: "100%", maxWidth: "100%", overflowX: "auto" }}
      >
        <Table size="small" sx={{ minWidth: 1000 }}>
          <TableHead>
            <TableRow>
              {COLUMNS.map((head, index) => {
                const isActions = index === COLUMNS.length - 1;
                const isSelect = head === "select";
                return (
                  <TableCell
                    key={head || index}
                    align={isActions ? "right" : "left"}
                    padding={isSelect ? "checkbox" : "normal"}
                    sx={{
                      typography: "s3",
                      fontWeight: 700,
                      textTransform: "uppercase",
                      letterSpacing: 0.4,
                      color: "text.subtitle",
                      bgcolor: "background.neutral",
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      whiteSpace: "nowrap",
                      ...(head === "#" && { width: 44 }),
                      ...(isSelect && { width: 44, pl: 1.5 }),
                      // Pinned, so the edit control stays reachable however far the situation and
                      // passes-when columns push the table. The shadow keeps it reading as one
                      // column rather than as content that happens to be at the edge.
                      ...(isActions && {
                        position: "sticky",
                        right: 0,
                        zIndex: 2,
                        width: 96,
                        minWidth: 96,
                        boxShadow: (theme) =>
                          `-8px 0 12px -6px ${alpha(
                            theme.palette.common.black,
                            theme.palette.mode === "dark" ? 0.45 : 0.08,
                          )}`,
                      }),
                    }}
                  >
                    {isSelect ? (
                      <Checkbox
                        size="small"
                        checked={allSelected}
                        indeterminate={someSelected}
                        onChange={toggleAll}
                        sx={selectableCheckboxSx}
                      />
                    ) : (
                      head
                    )}
                  </TableCell>
                );
              })}
            </TableRow>
          </TableHead>

          <TableBody>
            {!rows.length && (
              <TableRow>
                <TableCell
                  colSpan={COLUMNS.length}
                  sx={{ py: 4, textAlign: "center" }}
                >
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    No scenario matches that.
                  </Typography>
                  <Button
                    size="small"
                    onClick={() => {
                      setTyped("");
                      setFilters({});
                      setPage(0);
                    }}
                  >
                    Clear the filters
                  </Button>
                </TableCell>
              </TableRow>
            )}
            {rows.flatMap((scenario, index) => {
              // A header is emitted where a row's own group differs from the row above it. The
              // server orders the page by group, so that is exactly where a section starts, and
              // it stays correct even if the counts and the ordering ever disagree. Rebuilding
              // sections by slicing at offsets could not say that.
              const opens =
                scenario.group && scenario.group !== rows[index - 1]?.group;
              const inIt = opens
                ? rows.filter((one) => one.group === scenario.group)
                : [];
              const allOn =
                inIt.length > 0 && inIt.every((one) => selected.has(one.name));
              const someOn =
                !allOn && inIt.some((one) => selected.has(one.name));
              const section = totals.get(scenario.group);
              const drawn = [];
              if (opens) {
                drawn.push(
                  <TableRow key={`head-${scenario.group}`}>
                    <TableCell
                      colSpan={COLUMNS.length}
                      sx={{
                        bgcolor: (theme) =>
                          alpha(
                            theme.palette.text.primary,
                            theme.palette.mode === "dark" ? 0.08 : 0.05,
                          ),
                        py: 1,
                      }}
                    >
                      <Stack direction="row" spacing={1.25} alignItems="center">
                        <Checkbox
                          size="small"
                          checked={allOn}
                          indeterminate={someOn}
                          disabled={!inIt.length}
                          onChange={() => toggleGroup(inIt, allOn)}
                          sx={selectableCheckboxSx}
                        />
                        <Typography
                          sx={{
                            typography: "s2",
                            fontWeight: 700,
                            flex: 1,
                            minWidth: 0,
                          }}
                        >
                          {scenario.group}
                        </Typography>
                        <Typography
                          sx={{
                            typography: "s3",
                            fontWeight: 700,
                            color: "text.subtitle",
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {/* The group's real size, not the slice on this page. */}
                          {section?.total ?? inIt.length}{" "}
                          {(section?.total ?? inIt.length) === 1
                            ? "scenario"
                            : "scenarios"}
                          {section?.total > inIt.length
                            ? ` \u00b7 ${inIt.length} here`
                            : ""}
                        </Typography>
                      </Stack>
                    </TableCell>
                  </TableRow>,
                );
              }
              drawn.push(
                (() => {
                  const persona = scenario.persona || {};
                  const who = [
                    persona.gender,
                    persona.age_group,
                    persona.location,
                  ]
                    .filter(Boolean)
                    .join(" · ");
                  return (
                    <TableRow
                      hover
                      key={scenario.name}
                      onClick={() => setEditing(scenario)}
                      sx={{ cursor: "pointer" }}
                    >
                      <TableCell
                        padding="checkbox"
                        sx={{ pl: 1.5, verticalAlign: "top" }}
                        onClick={(event) => event.stopPropagation()}
                      >
                        <Checkbox
                          size="small"
                          checked={selected.has(scenario.name)}
                          onChange={() => toggle(scenario.name)}
                          sx={selectableCheckboxSx}
                        />
                      </TableCell>
                      <TableCell
                        sx={{
                          typography: "s3",
                          color: "text.subtitle",
                          fontVariantNumeric: "tabular-nums",
                          verticalAlign: "top",
                        }}
                      >
                        {/* The number is the scenario's place in the whole suite, minted by the
                              server, so it does not renumber under a filter or a page. */}
                        {scenario.number ?? index + 1}
                      </TableCell>
                      <TableCell sx={{ maxWidth: 280, verticalAlign: "top" }}>
                        <Typography
                          noWrap
                          sx={{ typography: "s2", fontWeight: 600 }}
                        >
                          {readable(scenario.name)}
                        </Typography>
                        <Tooltip title={scenario.branch || ""}>
                          <Typography
                            noWrap
                            sx={{ typography: "s3", color: "text.subtitle" }}
                          >
                            {scenario.branch}
                          </Typography>
                        </Tooltip>
                      </TableCell>
                      <TableCell sx={{ maxWidth: 200, verticalAlign: "top" }}>
                        <Typography noWrap sx={{ typography: "s2" }}>
                          {persona.name}
                        </Typography>
                        <Typography
                          noWrap
                          sx={{ typography: "s3", color: "text.subtitle" }}
                        >
                          {who}
                        </Typography>
                      </TableCell>
                      <TableCell sx={{ maxWidth: 220, verticalAlign: "top" }}>
                        <Levers
                          scenario={scenario}
                          persona={persona}
                          levelLabels={levelLabels}
                        />
                      </TableCell>
                      <TableCell sx={{ maxWidth: 320, verticalAlign: "top" }}>
                        <Clamped text={scenario.instruction} />
                      </TableCell>
                      <TableCell sx={{ maxWidth: 260, verticalAlign: "top" }}>
                        <SubGoals names={scenario.sub_goals} />
                      </TableCell>
                      <TableCell sx={{ maxWidth: 320, verticalAlign: "top" }}>
                        <Clamped text={scenario.tests} />
                      </TableCell>
                      <TableCell
                        align="right"
                        onClick={(event) => event.stopPropagation()}
                        sx={{
                          whiteSpace: "nowrap",
                          verticalAlign: "top",
                          position: "sticky",
                          right: 0,
                          zIndex: 1,
                          width: 96,
                          minWidth: 96,
                          // A sticky cell needs its own opaque ground to hide the columns sliding
                          // under it, which loses the row hover tint. Painting the same overlay
                          // back on keeps the pinned column part of the row rather than a patch.
                          bgcolor: "background.paper",
                          boxShadow: (theme) =>
                            `-8px 0 12px -6px ${alpha(
                              theme.palette.common.black,
                              theme.palette.mode === "dark" ? 0.45 : 0.08,
                            )}`,
                          transition: "background-image 120ms ease",
                          ".MuiTableRow-hover:hover &": {
                            backgroundImage: (theme) =>
                              `linear-gradient(${theme.palette.action.hover}, ${theme.palette.action.hover})`,
                          },
                        }}
                      >
                        <Tooltip
                          arrow
                          title={editable ? "Edit scenario" : lockedReason}
                        >
                          <span>
                            <IconButton
                              size="small"
                              aria-label="Edit scenario"
                              disabled={!editable}
                              onClick={() => setEditing(scenario)}
                            >
                              <Iconify
                                icon="solar:pen-new-square-linear"
                                width={15}
                                sx={{ color: "text.subtitle" }}
                              />
                            </IconButton>
                          </span>
                        </Tooltip>
                        <Tooltip
                          arrow
                          title={
                            editable ? "Remove from this suite" : lockedReason
                          }
                        >
                          <span>
                            <IconButton
                              size="small"
                              aria-label="Remove from this suite"
                              disabled={!editable || busy}
                              onClick={() => dropOne(scenario.name)}
                            >
                              <Iconify
                                icon="solar:trash-bin-trash-linear"
                                width={15}
                                sx={{ color: "text.subtitle" }}
                              />
                            </IconButton>
                          </span>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  );
                })(),
              );
              return drawn;
            })}
          </TableBody>
        </Table>
      </TableContainer>

      {jobId && (
        <DataTablePagination
          page={page}
          pageSize={pageSize}
          total={total}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(0);
          }}
        />
      )}

      {/* The suite's own properties, counted by the server over the whole filtered suite. Persona
          and coverage are reachable one level in, as `persona.age_group` and `coverage.overlay`. */}
      <FilterPanel
        anchorEl={filterAnchor}
        open={Boolean(filterAnchor)}
        onClose={() => setFilterAnchor(null)}
        filterFields={filterFields}
        currentFilters={activeFilters ? filters : null}
        onApply={applyFilters}
        aiPlaceholder="e.g. 'Indian accent callers carrying an attack'"
      />

      <Drawer
        anchor="right"
        open={Boolean(editing)}
        onClose={() => setEditing(null)}
      >
        <Box sx={{ width: "100vw", maxWidth: 620, height: "100%" }}>
          {editing && (
            <ScenarioEditForm
              scenario={editing}
              editableFields={editableFields}
              personaFields={personaFields}
              noiseChoices={noiseChoices}
              levelLabels={levelLabels}
              busy={busy}
              onCancel={() => setEditing(null)}
              onSave={saveScenario}
            />
          )}
        </Box>
      </Drawer>
    </Stack>
  );
}

// Coverage is its own filter: two axes rather than the list's properties, which is why it has its
// own controls and its own counts. The axis list and every cell come from the server.
function CoverageGrid({ coverage, rowAxis, colAxis, onRowAxis, onColAxis }) {
  const axes = coverage.axes || [];
  const columns = coverage.columns || [];
  const perAxis = coverage.per_axis || [];
  // Every name a reader sees here comes from the server. `readable` stays only as the fallback for
  // a level or axis served before the backend knew a name for it, so renaming one is a change
  // there and never here.
  const axisName = (axis) => coverage.axis_labels?.[axis] || readable(axis);
  const levelName = (level) =>
    coverage.level_labels?.[level] || readable(level);
  const cells = new Map(
    (coverage.cells || []).map((one) => [
      `${one.row}␟${one.column}`,
      one.count,
    ]),
  );
  return (
    <Stack spacing={1}>
      {/* Which axis was barely varied at all. The grid says which pairing is thin; this says
          whether an axis was used, which is the question asked first. */}
      {perAxis.length > 0 && (
        <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
          {perAxis.map((one) => (
            <Tooltip
              key={one.axis}
              title={Object.entries(one.counts || {})
                .map(([level, count]) => `${levelName(level)} ${count}`)
                .join("  \u00b7  ")}
            >
              <Chip
                size="small"
                label={`${one.label || axisName(one.axis)} ${one.levels}`}
                sx={{
                  height: 22,
                  typography: "s3",
                  // One level is not variation, so it reads as a gap rather than as coverage.
                  bgcolor: one.levels > 1 ? "action.hover" : "warning.lighter",
                  color: one.levels > 1 ? "text.secondary" : "warning.darker",
                }}
              />
            </Tooltip>
          ))}
        </Stack>
      )}
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        flexWrap="wrap"
        useFlexGap
      >
        <TextField
          select
          size="small"
          label="Rows"
          value={rowAxis}
          onChange={(event) => onRowAxis(event.target.value)}
          sx={{ minWidth: 170 }}
        >
          {axes.map((axis) => (
            <MenuItem key={axis} value={axis}>
              {axisName(axis)}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          label="Columns"
          value={colAxis}
          onChange={(event) => onColAxis(event.target.value)}
          sx={{ minWidth: 170 }}
        >
          {axes.map((axis) => (
            <MenuItem key={axis} value={axis}>
              {axisName(axis)}
            </MenuItem>
          ))}
        </TextField>
      </Stack>
      {(coverage.rows || []).length > 0 && columns.length > 0 && (
        <TableContainer sx={{ maxWidth: "100%", overflowX: "auto" }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ typography: "s3", color: "text.subtitle" }} />
                {columns.map((column) => (
                  <TableCell
                    key={column}
                    align="center"
                    sx={{
                      typography: "s3",
                      color: "text.subtitle",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {levelName(column)}
                  </TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {coverage.rows.map((row) => (
                <TableRow key={row}>
                  <TableCell sx={{ typography: "s3", whiteSpace: "nowrap" }}>
                    {levelName(row)}
                  </TableCell>
                  {columns.map((column) => {
                    const count = cells.get(`${row}␟${column}`) || 0;
                    return (
                      <TableCell
                        key={column}
                        align="center"
                        sx={{
                          typography: "s3",
                          fontVariantNumeric: "tabular-nums",
                          // An empty cell is the point of the grid, so it is drawn as a gap rather
                          // than as a zero competing with the counts around it.
                          color: count ? "text.primary" : "text.disabled",
                          bgcolor: (theme) =>
                            count
                              ? alpha(
                                  theme.palette.primary.main,
                                  Math.min(0.08 + count * 0.04, 0.32),
                                )
                              : "transparent",
                        }}
                      >
                        {count || "·"}
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Stack>
  );
}

CoverageGrid.propTypes = {
  coverage: PropTypes.object,
  rowAxis: PropTypes.string,
  colAxis: PropTypes.string,
  onRowAxis: PropTypes.func,
  onColAxis: PropTypes.func,
};

// The four things a voice suite is graded on: who is calling, in what accent and language, over
// what noise, and whether the call is an attack.
function Levers({ scenario, persona, levelLabels = {} }) {
  const coverage = scenario.coverage || {};
  const overlay = String(coverage.overlay || "").trim();
  const noise =
    typeof scenario.background_noise === "string"
      ? scenario.background_noise.trim()
      : scenario.background_noise
        ? "present"
        : "";
  const spoken = (persona.languages || []).filter(
    (one) => one && one !== "English",
  );
  const chips = [
    persona.accent && {
      key: `a-${persona.accent}`,
      label: persona.accent,
      tone: "default",
    },
    spoken.length && {
      key: `l-${spoken[0]}`,
      label: spoken[0],
      tone: "default",
    },
    noise && {
      key: `n-${noise}`,
      label: levelLabels[noise] ?? readable(noise),
      tone: "default",
    },
    overlay &&
      overlay !== "none" && {
        key: `o-${overlay}`,
        label: readable(overlay),
        tone: "adversarial",
      },
  ].filter(Boolean);

  if (!chips.length) {
    return (
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        &mdash;
      </Typography>
    );
  }
  const intensity = String(coverage.overlay_intensity || "").trim();
  return (
    <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap", gap: 0.5 }}>
      {chips.map((chip) => (
        <Tooltip
          key={chip.key}
          title={
            chip.tone === "adversarial" && intensity && intensity !== "absent"
              ? `${chip.label} \u00b7 ${intensity}`
              : ""
          }
        >
          <Chip
            size="small"
            label={chip.label}
            sx={{
              height: 20,
              typography: "s3",
              ...(chip.tone === "adversarial"
                ? { bgcolor: "warning.lighter", color: "warning.darker" }
                : { bgcolor: "action.hover", color: "text.secondary" }),
            }}
          />
        </Tooltip>
      ))}
    </Stack>
  );
}

Levers.propTypes = {
  scenario: PropTypes.object,
  persona: PropTypes.object,
  levelLabels: PropTypes.object,
};

function SubGoals({ names }) {
  const list = names || [];
  if (!list.length) {
    return (
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        &mdash;
      </Typography>
    );
  }
  const all = list
    .map((name, index) => `${index + 1}. ${readable(name)}`)
    .join("\n");
  return (
    <Tooltip title={all}>
      <Stack spacing={0.375}>
        {list.slice(0, 3).map((name, index) => (
          <Stack
            key={name}
            direction="row"
            spacing={0.75}
            alignItems="flex-start"
          >
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                fontVariantNumeric: "tabular-nums",
                flexShrink: 0,
                mt: "1px",
              }}
            >
              {index + 1}.
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.secondary", minWidth: 0 }}
            >
              {readable(name)}
            </Typography>
          </Stack>
        ))}
        {list.length > 3 && (
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", pl: 1.75 }}
          >
            + {list.length - 3} more
          </Typography>
        )}
      </Stack>
    </Tooltip>
  );
}

SubGoals.propTypes = { names: PropTypes.arrayOf(PropTypes.string) };

// Two lines, then ellipsis, with the whole value on hover. A situation can run to a paragraph and
// a table that lets one row grow to five lines stops being scannable.
function Clamped({ text }) {
  const value = String(text || "");
  return (
    <Tooltip title={value}>
      <Typography
        sx={{
          typography: "s2",
          color: "text.secondary",
          lineHeight: 1.45,
          display: "-webkit-box",
          WebkitLineClamp: 3,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          wordBreak: "break-word",
        }}
      >
        {value || "\u2014"}
      </Typography>
    </Tooltip>
  );
}

Clamped.propTypes = { text: PropTypes.string };

ScenarioSuite.propTypes = {
  scenarios: PropTypes.arrayOf(PropTypes.object),
  jobId: PropTypes.string,
  // A read-only suite is one with no job behind it, so there is nothing to save an edit to.
  editable: PropTypes.bool,
  scenarioEditing: PropTypes.shape({
    editable_fields: PropTypes.arrayOf(PropTypes.string),
    applied_without_rework: PropTypes.arrayOf(PropTypes.string),
  }),
  onChanged: PropTypes.func,
};
