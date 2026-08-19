import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, Grid, TextField, InputAdornment, Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { DATASETS, MIN_DATASET_ROWS, datasetRows } from "../../_mock/datasets";
import { scenariosFromDataset } from "../../_mock/scenarios";
import { SectionCard, EmptyState } from "../../components/primitives";
import { ThinkingBar } from "../../components/loading";
import { ScenarioRow } from "../ScenariosStep";

/**
 * Import scenarios from a dataset.
 *
 * Three steps in one pane, because they only make sense together: pick the
 * dataset, tick the columns that matter, then look at the scenarios those
 * columns produce before any of them are committed. The preview is generated
 * from the real column values, so ticking a different column visibly changes
 * what you are about to add.
 */
export default function DatasetImport({ env, onAdd, selected }) {
  const [datasetId, setDatasetId] = useState(DATASETS[0].id);
  const [query, setQuery] = useState("");
  const [cols, setCols] = useState(() =>
    DATASETS[0].columns.filter((c) => c.role !== "context").map((c) => c.key),
  );
  const [generated, setGenerated] = useState(null);
  const [busy, setBusy] = useState(false);
  const [checked, setChecked] = useState({});

  const dataset = DATASETS.find((d) => d.id === datasetId);
  const rows = useMemo(() => datasetRows(dataset), [dataset]);
  const alreadyIn = useMemo(() => new Set(selected.map((s) => s.id)), [selected]);

  const tooSmall = dataset.rowCount < MIN_DATASET_ROWS;
  const canGenerate = cols.length > 0 && !tooSmall;

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? DATASETS.filter((d) => d.name.toLowerCase().includes(q)) : DATASETS;
  }, [query]);

  const pickDataset = (d) => {
    setDatasetId(d.id);
    // Defaults that produce something useful immediately: the request and the
    // pass condition, leaving the incidental columns for the user to add.
    setCols(d.columns.filter((c) => c.role !== "context").map((c) => c.key));
    setGenerated(null);
    setChecked({});
  };

  const toggleCol = (key) =>
    setCols((c) => (c.includes(key) ? c.filter((k) => k !== key) : [...c, key]));

  const generate = () => {
    setBusy(true);
    setGenerated(null);
    setTimeout(() => {
      setGenerated(scenariosFromDataset(env, dataset, cols, rows));
      setBusy(false);
    }, 900);
  };

  const chosen = useMemo(
    () => (generated || []).filter((r) => checked[r.id]),
    [generated, checked],
  );

  return (
    <SectionCard
      title="Generate from a dataset"
      subtitle="Pick a dataset, choose the columns that matter, and turn its rows into tasks"
      action={
        <Button
          variant="contained"
          color="primary"
          size="small"
          disabled={chosen.length === 0}
          onClick={() => onAdd(chosen)}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Add {chosen.length || ""} {chosen.length === 1 ? "scenario" : "scenarios"}
        </Button>
      }
    >
      <Grid container spacing={2} sx={{ p: 2.5 }}>
        {/* ── 1. the dataset ── */}
        <Grid item xs={12} md={4}>
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
                  <Iconify icon="solar:magnifer-linear" width={15} sx={{ color: "text.subtitle" }} />
                </InputAdornment>
              ),
            }}
            sx={{ mb: 1.25, "& .MuiInputBase-input": { typography: "s2" } }}
          />
          <Stack spacing={1}>
            {shown.map((d) => (
              <DatasetRow
                key={d.id}
                dataset={d}
                active={d.id === datasetId}
                onClick={() => pickDataset(d)}
              />
            ))}
          </Stack>
        </Grid>

        {/* ── 2. the columns ── */}
        <Grid item xs={12} md={8}>
          <StepLabel n={2} label="Choose the columns to use" />
          <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1.5, overflow: "hidden" }}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={2}
              sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
            >
              <Box flex={1} minWidth={0}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{dataset.name}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {dataset.rowCount.toLocaleString()} rows · {dataset.columns.length} columns · updated {dataset.updated}
                </Typography>
              </Box>
              <Button
                size="small"
                onClick={() => setCols(dataset.columns.map((c) => c.key))}
                sx={{ typography: "s3", minWidth: 0, px: 0.75, color: "primary.main" }}
              >
                Select all
              </Button>
            </Stack>

            <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
              {dataset.columns.map((c) => (
                <ColumnRow
                  key={c.key}
                  column={c}
                  checked={cols.includes(c.key)}
                  onToggle={() => toggleCol(c.key)}
                />
              ))}
            </Stack>
          </Box>

          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mt: 1.5 }}>
            <Tooltip
              arrow
              title={tooSmall
                ? `${dataset.name} has only ${dataset.rowCount} rows — at least ${MIN_DATASET_ROWS} are needed`
                : cols.length === 0 ? "Tick at least one column" : ""}
            >
              <span>
                <Button
                  variant="contained"
                  color="primary"
                  size="small"
                  disabled={!canGenerate || busy}
                  onClick={generate}
                  startIcon={<Iconify icon="solar:magic-stick-3-linear" width={15} />}
                  sx={{ typography: "s2", fontWeight: 700 }}
                >
                  {generated ? "Regenerate scenarios" : "Generate scenarios"}
                </Button>
              </span>
            </Tooltip>
            <Typography sx={{ typography: "s3", color: tooSmall ? "error.main" : "text.subtitle" }}>
              {tooSmall
                ? `Only ${dataset.rowCount} rows — a minimum of ${MIN_DATASET_ROWS} is required.`
                : `${cols.length} of ${dataset.columns.length} columns selected`}
            </Typography>
          </Stack>
        </Grid>

        {/* ── 3. what came out ── */}
        <Grid item xs={12}>
          <StepLabel n={3} label="Review the scenarios" />
          <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1.5, overflow: "hidden" }}>
            {busy ? (
              <Box sx={{ px: 2, py: 1.5 }}>
                <ThinkingBar label={`Reading ${dataset.name}`} />
              </Box>
            ) : !generated ? (
              <EmptyState
                icon="solar:database-linear"
                title="Nothing generated yet"
                body="Pick your columns above and generate — the scenarios will appear here before anything is added."
              />
            ) : (
              <>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={2}
                  sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}
                >
                  <Box flex={1}>
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                      {generated.length} scenarios from {dataset.name}
                    </Typography>
                    <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                      One per row · click to include
                    </Typography>
                  </Box>
                  <Button
                    size="small"
                    onClick={() =>
                      setChecked(Object.fromEntries(generated.map((r) => [r.id, true])))
                    }
                    sx={{ typography: "s3", minWidth: 0, px: 0.75, color: "primary.main" }}
                  >
                    Select all
                  </Button>
                </Stack>
                <Stack divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}>
                  {generated.map((r, i) => (
                    <ScenarioRow
                      key={r.id}
                      row={r}
                      index={i}
                      selectable
                      checked={!!checked[r.id] || alreadyIn.has(r.id)}
                      onToggle={() =>
                        !alreadyIn.has(r.id) && setChecked((c) => ({ ...c, [r.id]: !c[r.id] }))
                      }
                    />
                  ))}
                </Stack>
              </>
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
          width: 20, height: 20, borderRadius: "50%", display: "grid", placeItems: "center",
          border: "1px solid", borderColor: "divider",
          typography: "s3", fontWeight: 700, color: "text.secondary",
        }}
      >
        {n}
      </Box>
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>{label}</Typography>
    </Stack>
  );
}
StepLabel.propTypes = { n: PropTypes.number, label: PropTypes.string };

function DatasetRow({ dataset, active, onClick }) {
  const small = dataset.rowCount < MIN_DATASET_ROWS;
  return (
    <Box
      onClick={onClick}
      sx={{
        p: 1.5, borderRadius: 1.25, cursor: "pointer",
        border: "1px solid",
        borderColor: active ? "primary.main" : "divider",
        bgcolor: (t) => active
          ? alpha(t.palette.primary.main, t.palette.mode === "dark" ? 0.12 : 0.05)
          : "background.paper",
        transition: "border-color .15s ease",
        "&:hover": { borderColor: active ? "primary.main" : "text.subtitle" },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <Box
          sx={{
            width: 28, height: 28, borderRadius: 0.875, flexShrink: 0,
            display: "grid", placeItems: "center",
            color: "text.secondary", bgcolor: "background.neutral",
          }}
        >
          <Iconify icon="solar:database-linear" width={15} />
        </Box>
        <Box flex={1} minWidth={0}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{dataset.name}</Typography>
          <Typography noWrap sx={{ typography: "s3", color: small ? "error.main" : "text.subtitle" }}>
            {dataset.rowCount.toLocaleString()} rows · {dataset.source}
          </Typography>
        </Box>
      </Stack>
    </Box>
  );
}
DatasetRow.propTypes = { dataset: PropTypes.object, active: PropTypes.bool, onClick: PropTypes.func };

function ColumnRow({ column, checked, onToggle }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      onClick={onToggle}
      sx={{ px: 2, py: 1.25, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
    >
      <Iconify
        icon={checked ? "solar:check-square-bold" : "solar:stop-linear"}
        width={17}
        sx={{ color: checked ? "primary.main" : "text.subtitle", flexShrink: 0 }}
      />
      <Box sx={{ width: 190, flexShrink: 0, minWidth: 0 }}>
        <Typography noWrap sx={{ typography: "s2", fontWeight: 600, fontFamily: "ui-monospace, Menlo, monospace" }}>
          {column.label}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{column.type}</Typography>
      </Box>
      <Typography noWrap sx={{ flex: 1, minWidth: 0, typography: "s3", color: "text.subtitle" }}>
        {column.sample}
      </Typography>
    </Stack>
  );
}
ColumnRow.propTypes = { column: PropTypes.object, checked: PropTypes.bool, onToggle: PropTypes.func };
