import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha, useTheme } from "@mui/material/styles";
import {
  Box,
  Stack,
  Typography,
  Button,
  Grid,
  TextField,
  InputAdornment,
  Checkbox,
  Select,
  MenuItem,
  Tooltip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  DATASETS,
  COLUMN_ROLES,
  allDatasetRows,
  defaultColumnRoles,
} from "../../_mock/datasets";
import { scenariosFromDataset, datasetScenarioId } from "../../_mock/scenarios";
import { sandboxFit, fitStatus } from "../../_mock/datasetWorld";
import { SectionCard } from "../../components/primitives";
import { clickable, focusRing } from "../../components/clickable";

/**
 * Import scenarios from a dataset.
 *
 * A dataset row is already a scenario — someone wrote or kept it because it
 * was worth testing. So nothing here is generated. Pick a dataset and its
 * rows appear, in its own columns; each column's header says what that
 * column becomes in a scenario (the caller's request, the expected outcome,
 * how the caller behaves, facts about their account, records the sandbox
 * must hold), pre-filled so most people never touch it. Tick rows, add
 * them. One row, one scenario; the only thing filled in is what a row can't
 * supply.
 *
 * Every row is checked against the environment's sandbox: it runs against a
 * seeded record, brings its own, or — when the sandbox has no such table —
 * can't run, and so can't be added.
 *
 * The data comes first because you can't say what a column is without
 * seeing what's in it — the mapping lives on the table, not in a step
 * before it. Rows are a picker, not an import-everything: a 1,284-row
 * dataset should not become 1,284 scenarios in one click.
 */
const PAGE = 20;

const CHECKBOX_SX = {
  p: 0.5,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

export default function DatasetImport({ env, onAdd, selected }) {
  const [datasetId, setDatasetId] = useState(DATASETS[0].id);
  const [query, setQuery] = useState("");
  const [roles, setRoles] = useState(() => defaultColumnRoles(DATASETS[0]));
  const [rowQuery, setRowQuery] = useState("");
  const [picked, setPicked] = useState(() => new Set());
  const [limit, setLimit] = useState(PAGE);

  const dataset = DATASETS.find((d) => d.id === datasetId);
  const rows = useMemo(() => allDatasetRows(dataset), [dataset]);
  const alreadyIn = useMemo(
    () => new Set(selected.map((s) => s.id)),
    [selected],
  );
  const added = (r) => alreadyIn.has(datasetScenarioId(env, dataset, r.__id));

  const colOf = (role) => dataset.columns.find((c) => roles[c.key] === role);
  const promptCol = colOf("prompt");
  const expectedCol = colOf("expected");
  const personaCol = colOf("persona");

  /* How the sandbox covers each row's records and account — recomputed when
     the mapping changes, since remapping a column to Record changes it. */
  const fits = useMemo(
    () =>
      new Map(rows.map((r) => [r.__id, sandboxFit(env, dataset, roles, r)])),
    [rows, roles, env, dataset],
  );
  const cantRun = (r) => fitStatus(fits.get(r.__id) || []) === "missing";
  const missingTables = useMemo(() => {
    const s = new Set();
    fits.forEach((f) =>
      f.filter((x) => x.status === "missing").forEach((x) => s.add(x.table)),
    );
    return [...s];
  }, [fits]);

  const shownDatasets = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q
      ? DATASETS.filter((d) => d.name.toLowerCase().includes(q))
      : DATASETS;
  }, [query]);

  const matching = useMemo(() => {
    const q = rowQuery.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) =>
      dataset.columns.some((c) => String(r[c.key]).toLowerCase().includes(q)),
    );
  }, [rows, rowQuery, dataset]);
  const pickable = matching.filter((r) => !added(r) && !cantRun(r));
  const allPicked =
    pickable.length > 0 && pickable.every((r) => picked.has(r.__id));

  const pickDataset = (d) => {
    setDatasetId(d.id);
    setRoles(defaultColumnRoles(d));
    setPicked(new Set());
    setRowQuery("");
    setLimit(PAGE);
  };

  /* Request, outcome and persona belong to one column each — giving one to a
     new column moves the old holder to context rather than losing it. */
  const setRole = (key, role) =>
    setRoles((r) => {
      const next = { ...r, [key]: role };
      if (COLUMN_ROLES.find((x) => x.id === role)?.unique) {
        Object.keys(next).forEach((k) => {
          if (k !== key && next[k] === role) next[k] = "context";
        });
      }
      return next;
    });

  const toggleRow = (id) =>
    setPicked((p) => {
      const next = new Set(p);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const selectAll = () =>
    setPicked((p) => new Set([...p, ...pickable.map((r) => r.__id)]));
  const clear = () => setPicked(new Set());

  /* A pick can go stale — remap a column and a picked row may stop being
     runnable — so what gets added is re-checked, not just remembered. */
  const chosen = rows.filter(
    (r) => picked.has(r.__id) && !added(r) && !cantRun(r),
  );
  const count = chosen.length;
  const commit = () => onAdd(scenariosFromDataset(env, dataset, roles, chosen));

  const filled = [
    !personaCol && "a persona is assigned to each row",
    !expectedCol && "passing means the request gets done",
  ].filter(Boolean);

  return (
    <SectionCard
      title="Import from a dataset"
      subtitle="Each row becomes one scenario"
    >
      <Grid container spacing={2} sx={{ p: 2.5 }}>
        {/* ── 1. the dataset ── */}
        <Grid item xs={12} md={3}>
          <StepLabel n={1} label="Choose a dataset" />
          <TextField
            size="small"
            fullWidth
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search datasets"
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify
                    icon="solar:magnifer-linear"
                    width={16}
                    sx={{ color: "text.tertiary" }}
                  />
                </InputAdornment>
              ),
            }}
            sx={{ mb: 1.25, "& .MuiInputBase-input": { typography: "s2" } }}
          />
          <Stack spacing={1}>
            {shownDatasets.map((d) => (
              <DatasetRow
                key={d.id}
                dataset={d}
                active={d.id === datasetId}
                onClick={() => pickDataset(d)}
              />
            ))}
          </Stack>
        </Grid>

        {/* ── 2. its rows, with what each column is on the header ── */}
        <Grid item xs={12} md={9} sx={{ minWidth: 0 }}>
          <StepLabel n={2} label="Pick the rows" />
          <Box
            sx={{
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
              overflow: "hidden",
            }}
          >
            {/* dataset + the action */}
            <Stack
              direction="row"
              alignItems="center"
              spacing={2}
              sx={{
                px: 2,
                py: 1.25,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Box flex={1} minWidth={0}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                  {dataset.name}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {dataset.rowCount.toLocaleString()} rows ·{" "}
                  {dataset.columns.length} columns · updated {dataset.updated}
                  {" · "}
                  <Tooltip
                    arrow
                    title="Adding copies these rows into the environment. Later edits to the dataset don't change the scenarios; each one records the dataset version it came from."
                  >
                    <Box
                      component="span"
                      sx={{
                        borderBottom: "1px dotted",
                        borderColor: "text.disabled",
                        cursor: "help",
                      }}
                    >
                      copied in when added
                    </Box>
                  </Tooltip>
                </Typography>
              </Box>
              <Button
                variant="contained"
                color="primary"
                size="small"
                disabled={count === 0 || !promptCol}
                onClick={commit}
                startIcon={
                  <Iconify icon="solar:add-circle-linear" width={16} />
                }
                sx={{ whiteSpace: "nowrap", minWidth: 148, flexShrink: 0 }}
              >
                {count > 0
                  ? `Add ${count.toLocaleString()} scenario${count === 1 ? "" : "s"}`
                  : "Add scenarios"}
              </Button>
            </Stack>

            {/* search + selection */}
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{
                px: 2,
                py: 1,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <TextField
                size="small"
                value={rowQuery}
                onChange={(e) => {
                  setRowQuery(e.target.value);
                  setLimit(PAGE);
                }}
                placeholder="Search rows"
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Iconify
                        icon="solar:magnifer-linear"
                        width={14}
                        sx={{ color: "text.tertiary" }}
                      />
                    </InputAdornment>
                  ),
                }}
                sx={{
                  width: 260,
                  "& .MuiInputBase-input": { typography: "s2" },
                }}
              />
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {rowQuery.trim()
                  ? `${matching.length.toLocaleString()} of ${rows.length.toLocaleString()} rows match`
                  : `${rows.length.toLocaleString()} rows`}
                {count > 0 && (
                  <Box
                    component="span"
                    sx={{ color: "text.secondary", fontWeight: 600 }}
                  >
                    {" · "}
                    {count.toLocaleString()} selected
                  </Box>
                )}
                {count > 100 && " — large suites make every run slower"}
              </Typography>
              <Button
                size="small"
                onClick={selectAll}
                disabled={allPicked || pickable.length === 0}
                sx={{
                  color: "primary.main",
                  minWidth: 0,
                  whiteSpace: "nowrap",
                  "&.Mui-disabled": { color: "text.disabled" },
                }}
              >
                {pickable.length === 0
                  ? "Select all"
                  : rowQuery.trim()
                    ? `Select ${pickable.length.toLocaleString()} matching`
                    : `Select all ${pickable.length.toLocaleString()}`}
              </Button>
              {count > 0 && (
                <Button
                  size="small"
                  onClick={clear}
                  sx={{ color: "text.secondary", minWidth: 0 }}
                >
                  Clear
                </Button>
              )}
            </Stack>

            {/* what the headers mean, and what the data doesn't cover */}
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{
                px: 2,
                py: 0.75,
                borderBottom: "1px solid",
                borderColor: "divider",
                bgcolor: (t) =>
                  promptCol ? "transparent" : alpha(t.palette.error.main, 0.06),
              }}
            >
              <Iconify
                icon={
                  promptCol
                    ? "solar:info-circle-linear"
                    : "solar:danger-circle-linear"
                }
                width={14}
                sx={{
                  color: promptCol ? "text.disabled" : "error.main",
                  flexShrink: 0,
                }}
              />
              <Typography
                sx={{
                  typography: "s3",
                  color: promptCol ? "text.tertiary" : "error.main",
                }}
              >
                {!promptCol
                  ? "Set one column to Caller's request — it's what each scenario's caller asks for."
                  : `Each column's header sets what it becomes in the scenario.${filled.length ? ` Not in the data, so filled in: ${filled.join("; ")}.` : ""}`}
                {promptCol && missingTables.length > 0 && (
                  <Box component="span" sx={{ color: "error.main" }}>
                    {` This environment's sandbox has no ${missingTables.join(" or ")} table, so rows that need one can't run. Set those columns to Context to add the rows without the records.`}
                  </Box>
                )}
              </Typography>
            </Stack>

            {matching.length === 0 ? (
              <Box sx={{ px: 2, py: 5, textAlign: "center" }}>
                <Typography sx={{ typography: "s2", color: "text.tertiary" }}>
                  No rows match your search.
                </Typography>
              </Box>
            ) : (
              <RowsTable
                dataset={dataset}
                rows={matching.slice(0, limit)}
                roles={roles}
                onRole={setRole}
                picked={picked}
                added={added}
                fits={fits}
                onToggle={toggleRow}
              />
            )}

            {matching.length > limit && (
              <Stack
                direction="row"
                alignItems="center"
                justifyContent="space-between"
                sx={{
                  px: 2,
                  py: 1,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  Showing {limit.toLocaleString()} of{" "}
                  {matching.length.toLocaleString()}
                </Typography>
                <Button
                  size="small"
                  onClick={() => setLimit((l) => l + PAGE)}
                  sx={{ color: "text.primary", minWidth: 0 }}
                >
                  Show {Math.min(PAGE, matching.length - limit)} more
                </Button>
              </Stack>
            )}
          </Box>
        </Grid>
      </Grid>
    </SectionCard>
  );
}

DatasetImport.propTypes = {
  env: PropTypes.object.isRequired,
  onAdd: PropTypes.func,
  selected: PropTypes.array,
};

function StepLabel({ n, label }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
      <Box
        sx={{
          width: 20,
          height: 20,
          borderRadius: "50%",
          display: "grid",
          placeItems: "center",
          border: "1px solid",
          borderColor: "divider",
          typography: "s3",
          fontWeight: 600,
          color: "text.secondary",
        }}
      >
        {n}
      </Box>
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
        {label}
      </Typography>
    </Stack>
  );
}
StepLabel.propTypes = { n: PropTypes.number, label: PropTypes.string };

function DatasetRow({ dataset, active, onClick }) {
  return (
    <Box
      {...clickable(onClick, { role: "radio", checked: active })}
      sx={{
        ...focusRing,
        p: 1.25,
        borderRadius: "8px",
        cursor: "pointer",
        border: "1px solid",
        borderColor: active ? "text.primary" : "divider",
        bgcolor: active ? "status.discovery.bg" : "background.paper",
        transition: "border-color .15s ease",
        "&:hover": { borderColor: active ? "text.primary" : "text.tertiary" },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <Box
          sx={{
            width: 28,
            height: 28,
            borderRadius: "6px",
            flexShrink: 0,
            display: "grid",
            placeItems: "center",
            color: "text.secondary",
            bgcolor: "background.neutral",
          }}
        >
          <Iconify icon="solar:database-linear" width={16} />
        </Box>
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
            {dataset.name}
          </Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
            {dataset.rowCount.toLocaleString()} rows · {dataset.source}
          </Typography>
        </Box>
      </Stack>
    </Box>
  );
}
DatasetRow.propTypes = {
  dataset: PropTypes.object,
  active: PropTypes.bool,
  onClick: PropTypes.func,
};

/**
 * The dataset's own rows, in its own columns. Each header carries the
 * column name and what it becomes in a scenario; an ignored column stays
 * visible but greyed, so you can still read it and change your mind. Only
 * the checkbox selects, the same as the production clusters.
 */
function RowsTable({
  dataset,
  rows,
  roles,
  onRole,
  picked,
  added,
  fits,
  onToggle,
}) {
  const theme = useTheme();
  /* Each column gets a floor wide enough for its header dropdown to read —
     the request most (it carries the sandbox lines too), other text next,
     short ones (ids, categories, numbers) least. Extra room is shared in
     the same proportion; below the floors the table scrolls sideways
     rather than truncating. Plain percentages — Chrome ignores calc() on
     table cells. */
  const minOf = (c) =>
    roles[c.key] === "prompt" ? 240 : c.type === "text" ? 180 : 104;
  const total = dataset.columns.reduce((w, c) => w + minOf(c), 0);
  const minTable = 44 + total;

  /* The request leads — it's the scenario's headline — and it and the
     checkbox stay pinned while a wide dataset scrolls sideways, so you never
     lose which row you're ticking. The rest keep the dataset's order. */
  const columns = [
    ...dataset.columns.filter((c) => roles[c.key] === "prompt"),
    ...dataset.columns.filter((c) => roles[c.key] !== "prompt"),
  ];
  const pinned = (c) => roles[c.key] === "prompt";
  const selectedTint = theme.palette.status.discovery.bg;
  /* Pinned body cells need a solid background to scroll over; the row's
     selected tint is laid on top of it so they still read as selected. */
  const pinBody = (left, selected) => ({
    position: "sticky",
    left,
    zIndex: 1,
    bgcolor: "background.paper",
    backgroundImage: selected
      ? `linear-gradient(${selectedTint}, ${selectedTint})`
      : "none",
  });

  return (
    <TableContainer sx={{ maxHeight: 520 }}>
      <Table
        stickyHeader
        size="small"
        sx={{ tableLayout: "fixed", width: "100%", minWidth: minTable }}
      >
        <TableHead>
          <TableRow
            sx={{
              /* No fill, and the theme's grey gradient off — like the product's DataTable. */
              "& .MuiTableCell-head": {
                bgcolor: "background.paper",
                backgroundImage: "none",
                /* Sticky cells don't paint borders in a collapsed table — a shadow draws the divider. */
                borderBottom: "none",
                boxShadow: (t) => `inset 0 -1px 0 ${t.palette.divider}`,
                py: 1,
                px: 1.25,
                verticalAlign: "top",
              },
            }}
          >
            <TableCell
              padding="checkbox"
              sx={{ width: 44, left: 0, zIndex: 3 }}
            />
            {columns.map((c) => {
              const ignored = roles[c.key] === "ignore";
              return (
                <TableCell
                  key={c.key}
                  sx={{
                    width: `${((minOf(c) / total) * 94).toFixed(2)}%`,
                    ...(pinned(c)
                      ? {
                          left: 44,
                          zIndex: 3,
                          boxShadow: (t) =>
                            `inset -1px 0 0 ${t.palette.divider}, inset 0 -1px 0 ${t.palette.divider}`,
                        }
                      : {}),
                  }}
                >
                  <Tooltip
                    title={`${c.label} · ${c.type}`}
                    placement="top-start"
                    arrow
                  >
                    <Typography
                      noWrap
                      sx={{
                        typography: "s3",
                        fontWeight: 600,
                        fontFamily: "ui-monospace, Menlo, monospace",
                        mb: 0.75,
                        color: ignored ? "text.disabled" : "text.primary",
                      }}
                    >
                      {c.label}
                    </Typography>
                  </Tooltip>
                  <RoleSelect
                    value={roles[c.key]}
                    onChange={(r) => onRole(c.key, r)}
                  />
                </TableCell>
              );
            })}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => {
            const isAdded = added(r);
            const rowFits = fits.get(r.__id) || [];
            const cantRun = fitStatus(rowFits) === "missing";
            const isPicked = picked.has(r.__id) && !cantRun;
            const lockReason = isAdded
              ? "Already added to this environment"
              : cantRun
                ? rowFits
                    .filter((f) => f.status === "missing")
                    .map((f) => f.text)
                    .join(" · ")
                : "";
            return (
              <TableRow
                key={r.__id}
                selected={isPicked}
                sx={{
                  "&.Mui-selected, &.Mui-selected:hover": {
                    bgcolor: "status.discovery.bg",
                  },
                  "& .MuiTableCell-body": {
                    borderBottom: "1px solid",
                    borderColor: "divider",
                    px: 1.25,
                    py: 1,
                    verticalAlign: "top",
                  },
                  "&:last-of-type .MuiTableCell-body": { borderBottom: "none" },
                }}
              >
                <TableCell
                  padding="checkbox"
                  sx={{ pt: "6px !important", ...pinBody(0, isPicked) }}
                >
                  <Tooltip title={lockReason} placement="right" arrow>
                    <span>
                      <Checkbox
                        size="small"
                        checked={isPicked || isAdded}
                        disabled={isAdded || cantRun}
                        onChange={() => onToggle(r.__id)}
                        sx={CHECKBOX_SX}
                      />
                    </span>
                  </Tooltip>
                </TableCell>
                {columns.map((c) => {
                  const role = roles[c.key];
                  const dim = isAdded || cantRun || role === "ignore";
                  return (
                    <TableCell
                      key={c.key}
                      sx={
                        pinned(c)
                          ? {
                              ...pinBody(44, isPicked),
                              boxShadow: (t) =>
                                `inset -1px 0 0 ${t.palette.divider}`,
                            }
                          : undefined
                      }
                    >
                      <Typography
                        sx={{
                          typography: role === "prompt" ? "s2" : "s3",
                          color: dim
                            ? "text.disabled"
                            : role === "prompt"
                              ? "text.primary"
                              : "text.secondary",
                          display: "-webkit-box",
                          WebkitLineClamp: 3,
                          WebkitBoxOrient: "vertical",
                          overflow: "hidden",
                          wordBreak: "break-word",
                        }}
                      >
                        {r[c.key]}
                      </Typography>
                      {/* where this row's records come from in the sandbox —
                          under the request, so it reads as part of the scenario */}
                      {role === "prompt" && rowFits.length > 0 && (
                        <SandboxLine fits={rowFits} />
                      )}
                    </TableCell>
                  );
                })}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
RowsTable.propTypes = {
  dataset: PropTypes.object,
  rows: PropTypes.array,
  roles: PropTypes.object,
  onRole: PropTypes.func,
  picked: PropTypes.instanceOf(Set),
  added: PropTypes.func,
  fits: PropTypes.instanceOf(Map),
  onToggle: PropTypes.func,
};

const FIT_ICON = {
  matched: { icon: "solar:check-circle-linear", color: "text.tertiary" },
  added: { icon: "solar:add-circle-linear", color: "text.tertiary" },
  missing: { icon: "solar:danger-circle-linear", color: "error.main" },
};

/**
 * One row's standing in the sandbox — a seeded record it runs against, a
 * record it brings with it, or a table the sandbox doesn't have.
 */
function SandboxLine({ fits }) {
  return (
    <Stack spacing={0.25} sx={{ mt: 0.5 }}>
      {fits.map((f) => (
        <Stack
          key={f.column}
          direction="row"
          alignItems="center"
          spacing={0.5}
          sx={{ minWidth: 0 }}
        >
          <Iconify
            icon={FIT_ICON[f.status].icon}
            width={12}
            sx={{ color: FIT_ICON[f.status].color, flexShrink: 0 }}
          />
          <Typography
            noWrap
            sx={{ typography: "s3", color: FIT_ICON[f.status].color }}
          >
            {f.text}
          </Typography>
        </Stack>
      ))}
    </Stack>
  );
}
SandboxLine.propTypes = { fits: PropTypes.array };

/** What a column becomes in a scenario — compact, to sit under its header. */
function RoleSelect({ value, onChange }) {
  return (
    <Select
      size="small"
      fullWidth
      value={value}
      onChange={(e) => onChange(e.target.value)}
      IconComponent={(p) => (
        <Iconify {...p} icon="solar:alt-arrow-down-linear" width={12} />
      )}
      renderValue={(v) => (
        <Typography
          noWrap
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: v === "ignore" ? "text.tertiary" : "text.primary",
          }}
        >
          {COLUMN_ROLES.find((r) => r.id === v)?.label}
        </Typography>
      )}
      sx={{
        bgcolor: "background.paper",
        minWidth: 0,
        "& .MuiSelect-select": {
          py: 0.5,
          pr: "24px !important",
          pl: 1,
          minWidth: 0,
        },
        "& .MuiSelect-icon": { color: "text.tertiary", right: 6 },
      }}
    >
      {COLUMN_ROLES.map((r) => (
        <MenuItem key={r.id} value={r.id} sx={{ display: "block", py: 0.75 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {r.label}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {r.hint}
          </Typography>
        </MenuItem>
      ))}
    </Select>
  );
}
RoleSelect.propTypes = { value: PropTypes.string, onChange: PropTypes.func };
