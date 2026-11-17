import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Autocomplete,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "src/components/snackbar";
import {
  NAME_ERROR,
  NAME_RULE,
  normalizeColumn,
  outputPathOptions,
  pathAppliesTo,
  removedToast,
  savedToast,
  upsertColumn,
} from "./customColumnModel";
import useCustomColumns, { fetchCustomColumnValues } from "./useCustomColumns";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";
const PREVIEW_ROWS = 5;
const PREVIEW_DEBOUNCE_MS = 300;

const show = (v) => {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
};

/**
 * W5-2 · `Add a column` (AG-16): a column from a path (`output.intent`) or an
 * expression (`len(expected_reply)`), for each run's result or for the row,
 * previewed on 5 rows of this view by the server (the one value function),
 * saved for everyone on the dataset.
 * Props: open, onClose, datasetId, view (R3 view: runIds, baselineId, pills…),
 *   runLabels {runId: label}, outputs (sample outputs for path suggestions),
 *   initial {name?, kind?, path?, expr?}, onSaved(def).
 */
const CustomColumnDialog = ({
  open,
  onClose,
  datasetId,
  view,
  runLabels = {},
  outputs = [],
  initial,
  onSaved,
}) => {
  const { columns, save, saving } = useCustomColumns(datasetId, {
    enabled: open,
  });
  const [name, setName] = useState("");
  const [kind, setKind] = useState("path");
  const [path, setPath] = useState("");
  const [expr, setExpr] = useState("");
  const [appliesTo, setAppliesTo] = useState("run");
  const [preview, setPreview] = useState({ state: "idle" });
  const [saveError, setSaveError] = useState(null);

  useEffect(() => {
    if (!open) return;
    setName(initial?.name ?? "");
    setKind(initial?.kind ?? (initial?.expr ? "expression" : "path"));
    setPath(initial?.path ?? "");
    setExpr(initial?.expr ?? "");
    setAppliesTo(initial?.applies_to ?? "run");
    setSaveError(null);
  }, [open, initial]);

  const pathOptions = useMemo(() => outputPathOptions(outputs), [outputs]);
  const draft = useMemo(
    () =>
      normalizeColumn({
        name: name.trim() || "draft",
        kind,
        path,
        expr,
        applies_to: kind === "path" ? pathAppliesTo(path) : appliesTo,
      }),
    [name, kind, path, expr, appliesTo],
  );
  const body = kind === "path" ? path.trim() : expr.trim();
  const nameBad = name.trim() !== "" && !NAME_RULE.test(name.trim());

  // Preview on 5 rows of this view, computed by the server.
  useEffect(() => {
    if (!open || !body || !view?.runIds?.length) {
      setPreview({ state: "idle" });
      return undefined;
    }
    let live = true;
    setPreview((p) => ({ ...p, state: "loading" }));
    const t = setTimeout(() => {
      fetchCustomColumnValues(datasetId, view, { draft })
        .then((res) => {
          if (!live) return;
          const ids = (res.example_ids ?? []).slice(0, PREVIEW_ROWS);
          const lines = ids.flatMap((id) =>
            Object.entries(res.values?.[id] ?? {}).map(([target, cols]) => ({
              row: res.row_orders?.[id] ?? id,
              target: target === "row" ? "Row" : runLabels[target] ?? target,
              value: cols.draft,
              error: res.cell_errors?.[id]?.[target]?.draft ?? null,
            })),
          );
          setPreview({
            state: "done",
            lines: lines.slice(0, PREVIEW_ROWS * 2),
            error: res.errors?.draft ?? null,
            type: res.columns?.[0]?.type ?? null,
          });
        })
        .catch((e) => {
          if (live)
            setPreview({
              state: "done",
              lines: [],
              error:
                typeof e?.result === "string" ? e.result : "Preview failed.",
            });
        });
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [open, body, draft, datasetId, view, runLabels]);

  const canSave =
    !!name.trim() &&
    !nameBad &&
    !!body &&
    preview.state === "done" &&
    !preview.error &&
    !saving;

  // HO R1: `Edit column…` from a results column group opens it with its id.
  const editingId = initial?.id ?? null;
  const submit = async () => {
    if (!canSave) return;
    const def = {
      ...draft,
      ...(editingId ? { id: editingId } : {}),
      name: name.trim(),
    };
    try {
      const saved = await save(upsertColumn(columns, def));
      enqueueSnackbar(savedToast(def.name), { variant: "success" });
      onSaved?.(saved.find((d) => d.name === def.name) ?? def);
      onClose();
    } catch (e) {
      setSaveError(
        typeof e?.result === "string" ? e.result : "Could not save the column.",
      );
    }
  };
  const remove = async () => {
    if (!editingId || saving) return;
    try {
      await save(columns.filter((d) => d.id !== editingId));
      enqueueSnackbar(removedToast(initial?.name ?? name.trim()), {
        variant: "success",
      });
      onClose();
    } catch (e) {
      setSaveError(
        typeof e?.result === "string"
          ? e.result
          : "Could not remove the column.",
      );
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      PaperProps={{ sx: { width: 600, maxWidth: "calc(100vw - 32px)" } }}
      data-testid="custom-column-dialog"
    >
      <DialogTitle sx={{ pb: 1 }}>
        {editingId ? `Edit column “${initial?.name ?? ""}”` : "Add a column"}
      </DialogTitle>
      <DialogContent sx={{ pt: "6px !important" }}>
        <Stack spacing={1.5}>
          <TextField
            size="small"
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            error={nameBad}
            helperText={
              nameBad ? NAME_ERROR : `Use it as col.${name.trim() || "name"}`
            }
            inputProps={{ "data-testid": "custom-column-name" }}
          />
          <RadioGroup value={kind} onChange={(e) => setKind(e.target.value)}>
            <FormControlLabel
              value="path"
              control={<Radio size="small" />}
              label="From a path"
            />
            {kind === "path" && (
              <Autocomplete
                freeSolo
                size="small"
                options={pathOptions}
                getOptionLabel={(o) => (typeof o === "string" ? o : o.path)}
                renderOption={(props, o) => (
                  <li {...props} key={o.path}>
                    <Box component="span" sx={{ fontFamily: MONO, flex: 1 }}>
                      {o.path}
                    </Box>
                    <Typography typography="s3" color="text.secondary">
                      {o.count} of {Math.min(outputs.length, 20)} outputs
                    </Typography>
                  </li>
                )}
                inputValue={path}
                onInputChange={(_, v) => setPath(v ?? "")}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    placeholder="output.intent"
                    inputProps={{
                      ...params.inputProps,
                      "data-testid": "custom-column-path",
                      style: { fontFamily: MONO },
                    }}
                  />
                )}
                sx={{ ml: 4 }}
              />
            )}
            <FormControlLabel
              value="expression"
              control={<Radio size="small" />}
              label="From an expression"
            />
            {kind === "expression" && (
              <TextField
                size="small"
                placeholder="len(expected_reply)"
                value={expr}
                onChange={(e) => setExpr(e.target.value)}
                inputProps={{
                  "data-testid": "custom-column-expr",
                  style: { fontFamily: MONO },
                }}
                sx={{ ml: 4 }}
              />
            )}
          </RadioGroup>
          <Stack direction="row" alignItems="center" gap={1}>
            <Typography typography="s2" color="text.secondary">
              Applies to
            </Typography>
            <RadioGroup
              row
              value={kind === "path" ? pathAppliesTo(path) : appliesTo}
              onChange={(e) => setAppliesTo(e.target.value)}
            >
              <FormControlLabel
                value="run"
                disabled={kind === "path"}
                control={<Radio size="small" />}
                label="each run's result"
              />
              <FormControlLabel
                value="row"
                disabled={kind === "path"}
                control={<Radio size="small" />}
                label="the row"
              />
            </RadioGroup>
          </Stack>
          <Box data-testid="custom-column-preview">
            <Typography typography="s2" color="text.secondary" sx={{ mb: 0.5 }}>
              Preview on {PREVIEW_ROWS} rows of this view
              {preview.type ? ` · ${preview.type}` : ""}
            </Typography>
            {preview.state === "loading" && (
              <Typography typography="s2">Computing…</Typography>
            )}
            {preview.error && (
              <Typography
                typography="s2"
                color="error.main"
                data-testid="custom-column-error"
              >
                {preview.error}
              </Typography>
            )}
            {preview.state === "done" &&
              !preview.error &&
              (preview.lines ?? []).map((l) => (
                <Stack
                  key={`${l.row}-${l.target}`}
                  direction="row"
                  gap={1}
                  sx={{ fontFamily: MONO, fontSize: 12.5 }}
                >
                  <Box sx={{ width: 64, color: "text.secondary" }}>
                    Row {l.row}
                  </Box>
                  <Box sx={{ width: 64, color: "text.secondary" }}>
                    {l.target}
                  </Box>
                  <Box
                    sx={{
                      flex: 1,
                      minWidth: 0,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      color: l.error ? "error.main" : "text.primary",
                    }}
                  >
                    {l.error ? l.error : show(l.value)}
                  </Box>
                </Stack>
              ))}
          </Box>
          {saveError && (
            <Typography typography="s2" color="error.main">
              {saveError}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        {editingId && (
          <Button
            size="small"
            color="error"
            onClick={remove}
            disabled={saving}
            data-testid="custom-column-remove"
            sx={{ mr: "auto" }}
          >
            Remove column
          </Button>
        )}
        <Button size="small" onClick={onClose}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          disabled={!canSave}
          onClick={submit}
          data-testid="custom-column-save"
        >
          Save column
        </Button>
      </DialogActions>
    </Dialog>
  );
};

CustomColumnDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  datasetId: PropTypes.string,
  view: PropTypes.object,
  runLabels: PropTypes.object,
  outputs: PropTypes.array,
  initial: PropTypes.object,
  onSaved: PropTypes.func,
};

export default CustomColumnDialog;
