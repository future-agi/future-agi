import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { enqueueSnackbar } from "notistack";
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
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  COLUMN_ROLES,
  SAMPLE_DATASETS,
  datasetScenarioId,
  defaultColumnRoles,
  fitStatus,
  sampleDatasetRows,
  sandboxFit,
} from "src/api/simulate-environments/_fixtures/detail/scenarioSources";
import SectionCard from "../../../components/SectionCard";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../../gaps/NeedsInputButton";
import { ENV_SHAPE, ENV_STATE_SHAPE } from "../scenarios.shapes";

const PAGE = 20;

const CHECKBOX_SX = {
  p: 0.5,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
  "&.MuiCheckbox-indeterminate": { color: "text.primary" },
};

const selectedTintOf = (theme) => alpha(theme.palette.primary.main, 0.08);

// Vel's dataset import. A dataset row is already a scenario, so nothing here
// is generated: pick a dataset, its rows appear in its own columns, and each
// column's header says what that column becomes in a scenario. Every row is
// checked against the environment's sandbox (env.seed.tables): it runs
// against a seeded record, brings its own, or can't run and can't be added.
// No backend yet: the datasets and rows are sample data and adding saves
// nothing.
export default function DatasetRoute({ env, envState, onClose }) {
  const theme = useTheme();
  const [datasetId, setDatasetId] = useState(SAMPLE_DATASETS[0].id);
  const [query, setQuery] = useState("");
  const [roles, setRoles] = useState(() =>
    defaultColumnRoles(SAMPLE_DATASETS[0]),
  );
  const [rowQuery, setRowQuery] = useState("");
  const [picked, setPicked] = useState(() => new Set());
  const [limit, setLimit] = useState(PAGE);

  const dataset = SAMPLE_DATASETS.find((d) => d.id === datasetId);
  const rows = useMemo(() => sampleDatasetRows(dataset), [dataset]);
  const alreadyIn = useMemo(
    () => new Set((envState?.scenarios || []).map((s) => s.id)),
    [envState],
  );
  const added = (r) => alreadyIn.has(datasetScenarioId(env, dataset, r.__id));

  const colOf = (role) => dataset.columns.find((c) => roles[c.key] === role);
  const promptCol = colOf("prompt");
  const expectedCol = colOf("expected");
  const personaCol = colOf("persona");

  // Recomputed when the mapping changes, since remapping a column to Record
  // changes how the sandbox covers a row.
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
      ? SAMPLE_DATASETS.filter((d) => d.name.toLowerCase().includes(q))
      : SAMPLE_DATASETS;
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

  // Request, outcome and persona belong to one column each: giving one to a
  // new column moves the old holder to Context rather than losing it.
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

  // A pick can go stale (remap a column and a picked row may stop being
  // runnable), so what gets added is re-checked, not just remembered.
  const chosen = rows.filter(
    (r) => picked.has(r.__id) && !added(r) && !cantRun(r),
  );
  const count = chosen.length;
  const commit = () => {
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
    onClose();
  };

  const filled = [
    !personaCol && "a persona is assigned to each row",
    !expectedCol && "passing means the request gets done",
  ].filter(Boolean);

  return (
    <SectionCard
      title={
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography
            component="span"
            sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}
          >
            Import from a dataset
          </Typography>
          <UiOnlyFlag feature="scenarioSources" />
        </Stack>
      }
      subtitle="Each row becomes one scenario"
    >
      <Grid container spacing={2} sx={{ p: 2.5 }}>
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
                    sx={{ color: "text.subtitle" }}
                  />
                </InputAdornment>
              ),
            }}
            sx={{ mb: 1.25, "& .MuiInputBase-input": { typography: "s2" } }}
          />
          <Stack spacing={1} role="radiogroup" aria-label="Datasets">
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
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {`${dataset.rowCount.toLocaleString()} rows · ${dataset.columns.length} columns · updated ${dataset.updated} · `}
                  <CustomTooltip
                    show
                    arrow
                    size="small"
                    title="Copies these rows in; later dataset edits don't change the scenarios."
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
                  </CustomTooltip>
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
                        sx={{ color: "text.subtitle" }}
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
                  color: "text.subtitle",
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
                    {` · ${count.toLocaleString()} selected`}
                  </Box>
                )}
                {count > 100 && ". Large suites make every run slower."}
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
                  color: promptCol ? "text.subtitle" : "error.main",
                }}
              >
                {!promptCol
                  ? "Set one column to Caller's request. It's what each scenario's caller asks for."
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
                <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
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
                selectedTint={selectedTintOf(theme)}
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
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {`Showing ${limit.toLocaleString()} of ${matching.length.toLocaleString()}`}
                </Typography>
                <Button
                  size="small"
                  onClick={() => setLimit((l) => l + PAGE)}
                  sx={{ color: "text.primary", minWidth: 0 }}
                >
                  {`Show ${Math.min(PAGE, matching.length - limit)} more`}
                </Button>
              </Stack>
            )}
          </Box>
        </Grid>
      </Grid>
    </SectionCard>
  );
}
DatasetRoute.propTypes = {
  env: ENV_SHAPE,
  envState: ENV_STATE_SHAPE,
  onClose: PropTypes.func.isRequired,
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
      role="radio"
      aria-checked={active}
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      sx={{
        p: 1.25,
        borderRadius: "8px",
        cursor: "pointer",
        border: "1px solid",
        borderColor: active ? "text.primary" : "divider",
        bgcolor: active ? (t) => selectedTintOf(t) : "background.paper",
        transition: "border-color .15s ease",
        "&:hover": { borderColor: active ? "text.primary" : "text.subtitle" },
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
          outlineOffset: 1,
        },
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
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            {`${dataset.rowCount.toLocaleString()} rows · ${dataset.source}`}
          </Typography>
        </Box>
      </Stack>
    </Box>
  );
}
DatasetRow.propTypes = {
  dataset: PropTypes.object.isRequired,
  active: PropTypes.bool,
  onClick: PropTypes.func,
};

// The dataset's own rows in its own columns. Each header carries the column
// name and what it becomes in a scenario; an ignored column stays visible but
// greyed. The request column and the checkbox stay pinned while a wide
// dataset scrolls sideways.
function RowsTable({
  dataset,
  rows,
  roles,
  onRole,
  picked,
  added,
  fits,
  onToggle,
  selectedTint,
}) {
  // Each column gets a floor wide enough for its header dropdown; below the
  // floors the table scrolls sideways rather than truncating.
  const minOf = (c) =>
    roles[c.key] === "prompt" ? 240 : c.type === "text" ? 180 : 104;
  const total = dataset.columns.reduce((w, c) => w + minOf(c), 0);
  const minTable = 44 + total;

  const columns = [
    ...dataset.columns.filter((c) => roles[c.key] === "prompt"),
    ...dataset.columns.filter((c) => roles[c.key] !== "prompt"),
  ];
  const pinned = (c) => roles[c.key] === "prompt";
  // Pinned body cells need a solid background to scroll over; the selected
  // tint is laid on top so they still read as selected.
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
              "& .MuiTableCell-head": {
                bgcolor: "background.paper",
                backgroundImage: "none",
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
                  <CustomTooltip
                    show
                    arrow
                    size="small"
                    placement="top-start"
                    title={`${c.label} · ${c.type}`}
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
                  </CustomTooltip>
                  <RoleSelect
                    column={c.label}
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
                    bgcolor: selectedTint,
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
                  <CustomTooltip
                    show={!!lockReason}
                    arrow
                    size="small"
                    placement="right"
                    title={lockReason}
                  >
                    <span>
                      <Checkbox
                        size="small"
                        checked={isPicked || isAdded}
                        disabled={isAdded || cantRun}
                        onChange={() => onToggle(r.__id)}
                        inputProps={{ "aria-label": `Select row ${r.__i + 1}` }}
                        sx={CHECKBOX_SX}
                      />
                    </span>
                  </CustomTooltip>
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
  dataset: PropTypes.object.isRequired,
  rows: PropTypes.array.isRequired,
  roles: PropTypes.object.isRequired,
  onRole: PropTypes.func.isRequired,
  picked: PropTypes.instanceOf(Set).isRequired,
  added: PropTypes.func.isRequired,
  fits: PropTypes.instanceOf(Map).isRequired,
  onToggle: PropTypes.func.isRequired,
  selectedTint: PropTypes.string.isRequired,
};

const FIT_ICON = {
  matched: { icon: "solar:check-circle-linear", color: "text.subtitle" },
  added: { icon: "solar:add-circle-linear", color: "text.subtitle" },
  missing: { icon: "solar:danger-circle-linear", color: "error.main" },
};

// One row's standing in the sandbox: a seeded record it runs against, a
// record it brings with it, or a table the sandbox doesn't have.
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
SandboxLine.propTypes = { fits: PropTypes.array.isRequired };

function RoleSelectIcon(props) {
  return <Iconify {...props} icon="solar:alt-arrow-down-linear" width={12} />;
}

// What a column becomes in a scenario, compact enough to sit under its header.
function RoleSelect({ column, value, onChange }) {
  return (
    <Select
      size="small"
      fullWidth
      value={value}
      onChange={(e) => onChange(e.target.value)}
      IconComponent={RoleSelectIcon}
      inputProps={{ "aria-label": `${column} becomes` }}
      renderValue={(v) => (
        <Typography
          noWrap
          sx={{
            typography: "s3",
            fontWeight: 600,
            color: v === "ignore" ? "text.subtitle" : "text.primary",
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
        "& .MuiSelect-icon": { color: "text.subtitle", right: 6 },
      }}
    >
      {COLUMN_ROLES.map((r) => (
        <MenuItem key={r.id} value={r.id} sx={{ display: "block", py: 0.75 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {r.label}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {r.hint}
          </Typography>
        </MenuItem>
      ))}
    </Select>
  );
}
RoleSelect.propTypes = {
  column: PropTypes.string.isRequired,
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};
