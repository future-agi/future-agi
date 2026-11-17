// W5-5 (W5-SCOPE §7 C2): the leaderboard editor. Datasets (several),
// `Runs where [...]` in the one expression language over the runs catalogue
// (validated live, the same check the server runs on save), Columns, Sort.
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Autocomplete,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { compileExpression } from "src/sections/common/filters/expression";
import { useTags } from "src/sections/common/Tags/useTags";
import {
  bestFirstDir,
  definitionErrors,
  normalizeSort,
  rulePlaceholder,
} from "./leaderboardModel";
import {
  boardErrorOf,
  useBoardDatasets,
  useBoardOptions,
  useCreateLeaderboard,
  useUpdateLeaderboard,
} from "./useLeaderboards";

const EMPTY = {
  name: "",
  dataset_ids: [],
  run_filter: "",
  columns: [],
  sort: null,
};

export default function LeaderboardEditor({ open, onClose, initial, onSaved }) {
  const [form, setForm] = useState(EMPTY);
  const [serverError, setServerError] = useState(null);
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (open) {
      setForm(initial ? { ...EMPTY, ...initial } : EMPTY);
      setServerError(null);
      setTouched(false);
    }
  }, [open, initial]);

  const { data: datasets = [] } = useBoardDatasets();
  const opts = useBoardOptions(form.dataset_ids);
  const defsByKey = useMemo(
    () => Object.fromEntries(opts.columns.map((c) => [c.key, c])),
    [opts.columns],
  );
  const create = useCreateLeaderboard();
  const update = useUpdateLeaderboard();
  const saving = create.isPending || update.isPending;
  // L7c g1 (wlb:editor): the example rule names a tag this workspace has.
  const { data: tagReg } = useTags({ enabled: open });
  const placeholder = rulePlaceholder(tagReg?.tags);

  const filterError = useMemo(() => {
    const text = form.run_filter.trim();
    if (!text || opts.loading) return null;
    const res = compileExpression(text, opts.catalog);
    return res.error ? res.error.message : null;
  }, [form.run_filter, opts.catalog, opts.loading]);

  const sort = normalizeSort(form.sort, form.columns, defsByKey);
  const errors = {
    ...definitionErrors(form, {
      datasetIds: new Set(datasets.map((d) => d.id)),
      columnKeys: opts.loading ? null : new Set(opts.columns.map((c) => c.key)),
    }),
    ...(filterError ? { run_filter: filterError } : {}),
    ...(serverError?.errors ?? {}),
  };
  const shown = (k) => (touched || serverError ? errors[k] : null);
  const set = (patch) => {
    setServerError(null);
    setForm((f) => ({ ...f, ...patch }));
  };

  const save = async () => {
    setTouched(true);
    const body = { ...form, sort };
    if (Object.keys(definitionErrors(body)).length || filterError) return;
    try {
      const res = initial?.id
        ? await update.mutateAsync({ id: initial.id, ...body })
        : await create.mutateAsync(body);
      onSaved?.(res?.data?.result);
      onClose();
    } catch (e) {
      setServerError(boardErrorOf(e));
    }
  };

  const chosenDatasets = datasets.filter((d) =>
    form.dataset_ids.includes(d.id),
  );
  const chosenColumns = form.columns
    .map((c) => defsByKey[c.key] ?? { key: c.key, label: c.label ?? c.key })
    .filter(Boolean);

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>
        {initial?.id ? "Edit leaderboard" : "New leaderboard"}
      </DialogTitle>
      <DialogContent>
        <Stack gap={2} sx={{ pt: 1 }} data-testid="leaderboard-editor">
          <TextField
            label="Name"
            size="small"
            value={form.name}
            onChange={(e) => set({ name: e.target.value })}
            error={!!shown("name")}
            helperText={shown("name") || " "}
            autoFocus
          />
          <Autocomplete
            multiple
            size="small"
            options={datasets}
            value={chosenDatasets}
            getOptionLabel={(d) => d.name}
            isOptionEqualToValue={(a, b) => a.id === b.id}
            onChange={(_, v) => set({ dataset_ids: v.map((d) => d.id) })}
            renderTags={(v, getTagProps) =>
              v.map((d, i) => (
                <Chip
                  size="small"
                  label={d.name}
                  {...getTagProps({ index: i })}
                  key={d.id}
                />
              ))
            }
            renderInput={(params) => (
              <TextField
                {...params}
                label="Datasets"
                error={!!shown("dataset_ids")}
                helperText={
                  shown("dataset_ids") || "Runs on any of these datasets"
                }
              />
            )}
          />
          <TextField
            label="Runs where"
            size="small"
            value={form.run_filter}
            onChange={(e) => set({ run_filter: e.target.value })}
            placeholder={placeholder}
            error={!!errors.run_filter}
            helperText={
              errors.run_filter ||
              (form.run_filter.trim()
                ? "Finished runs that match, read again on every visit"
                : "Empty: every finished run on these datasets")
            }
            inputProps={{
              "aria-label": "Runs where",
              style: { fontFamily: "monospace" },
            }}
          />
          <Autocomplete
            multiple
            size="small"
            options={opts.columns}
            value={chosenColumns}
            getOptionLabel={(c) => c.label ?? c.name ?? c.key}
            isOptionEqualToValue={(a, b) => a.key === b.key}
            onChange={(_, v) =>
              set({ columns: v.map((c) => ({ key: c.key, label: null })) })
            }
            renderOption={(props, c) => (
              <li {...props} key={c.key}>
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  sx={{ width: "100%" }}
                >
                  <span>{c.label}</span>
                  {form.dataset_ids.length > 1 && (
                    <Typography variant="caption" color="text.secondary">
                      {`${c.datasets} of ${form.dataset_ids.length} datasets`}
                    </Typography>
                  )}
                </Stack>
              </li>
            )}
            renderInput={(params) => (
              <TextField
                {...params}
                label="Columns"
                error={!!shown("columns")}
                helperText={
                  shown("columns") ||
                  (form.dataset_ids.length
                    ? " "
                    : "Pick datasets to see their evals")
                }
              />
            )}
          />
          <Stack direction="row" gap={1.5}>
            <TextField
              select
              size="small"
              label="Sort by"
              value={sort.column ?? ""}
              onChange={(e) =>
                set({
                  sort: {
                    column: e.target.value,
                    dir: bestFirstDir(defsByKey[e.target.value]),
                  },
                })
              }
              disabled={!chosenColumns.length}
              sx={{ flex: 1 }}
            >
              {chosenColumns.map((c) => (
                <MenuItem key={c.key} value={c.key}>
                  {c.label}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label="Order"
              value={sort.dir}
              onChange={(e) => set({ sort: { ...sort, dir: e.target.value } })}
              disabled={!chosenColumns.length}
              sx={{ width: 180 }}
            >
              <MenuItem value="desc">Highest first</MenuItem>
              <MenuItem value="asc">Lowest first</MenuItem>
            </TextField>
          </Stack>
          {serverError?.message && (
            <Typography variant="body2" color="error.main">
              {serverError.message}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={save}
          disabled={saving || !!filterError}
        >
          {initial?.id ? "Save" : "Create leaderboard"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

LeaderboardEditor.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  initial: PropTypes.object,
  onSaved: PropTypes.func,
};
