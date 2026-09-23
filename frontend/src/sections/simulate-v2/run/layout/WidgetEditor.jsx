import PropTypes from "prop-types";
import { useMemo, useState, useEffect } from "react";
import { alpha } from "@mui/material/styles";
import {
  Dialog, DialogTitle, DialogContent, DialogActions,
  Box, Stack, Typography, TextField, MenuItem, Button, IconButton,
  Chip, Autocomplete,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  DATA_SOURCES, METRICS, GROUP_BYS, CHART_TYPES,
  FILTER_FIELDS, FILTER_OPS,
  CustomWidgetBody, distinctValues,
} from "./customWidgetRenderer";

/**
 * Editor dialog for adding / editing a custom analytics widget.
 *
 * Two-pane layout: form on the left (title · source · metric · chart
 * · group-by · filters), live preview on the right. Preview mounts
 * the same renderer that ships to the layout so "what you see" is
 * exactly what appears on the tab after Save.
 */

function newDraft() {
  return {
    id: `custom-${Math.random().toString(36).slice(2, 10)}`,
    kind: "custom",
    title: "New widget",
    source: "tasks",
    chart: "bar",
    metric: "count",
    groupBy: "persona",
    subGroupBy: null,
    limit: 8,
    filters: [],
  };
}

const MATRIX_CHARTS = new Set(["heatmap", "stacked_bar"]);

export default function WidgetEditor({ open, onClose, onSave, ctx, initial, restrictCharts }) {
  const [draft, setDraft] = useState(() => initial || newDraft());
  useEffect(() => {
    if (open) setDraft(initial ? { ...initial, filters: initial.filters || [] } : newDraft());
  }, [open, initial]);

  const patch = (k, v) => setDraft((d) => {
    const next = { ...d, [k]: v };
    /* Keep the config internally consistent when the user pivots a
       dimension — otherwise we ship configs that reference metrics
       or dims that no longer apply and the renderer's "no data"
       fallback fires for a bad reason. */
    if (k === "source") {
      const validMetrics = METRICS.filter((m) => (m.forSource || []).includes(v));
      if (!validMetrics.some((m) => m.id === next.metric)) next.metric = validMetrics[0]?.id || "count";
      const validGroup = GROUP_BYS.filter((g) => !g.forSource || g.forSource.includes(v));
      if (!validGroup.some((g) => g.id === next.groupBy)) next.groupBy = validGroup[0]?.id || "persona";
      if (next.subGroupBy && !validGroup.some((g) => g.id === next.subGroupBy)) next.subGroupBy = null;
      next.filters = (next.filters || []).filter((f) => FILTER_FIELDS.some((ff) => ff.id === f.field));
    }
    if (k === "chart") {
      if (v === "bignumber") { next.groupBy = null; next.subGroupBy = null; }
      else if (!next.groupBy) next.groupBy = "persona";
      if (MATRIX_CHARTS.has(v) && !next.subGroupBy) {
        next.subGroupBy = pickSubGroupBy(next.groupBy);
      }
      if (!MATRIX_CHARTS.has(v)) next.subGroupBy = null;
    }
    return next;
  });

  const eligibleMetrics = useMemo(
    () => METRICS.filter((m) => (m.forSource || []).includes(draft.source)),
    [draft.source],
  );
  const eligibleGroups = useMemo(
    () => GROUP_BYS.filter((g) => !g.forSource || g.forSource.includes(draft.source)),
    [draft.source],
  );

  const canSave = draft.title.trim().length > 0;
  const isMatrix = MATRIX_CHARTS.has(draft.chart);

  /* ── filter editing ───────────────────────────────────────── */
  const addFilter = () => {
    const first = FILTER_FIELDS[0];
    patch("filters", [...(draft.filters || []), { field: first.id, op: "is", values: [] }]);
  };
  const updateFilter = (idx, next) => {
    patch("filters", draft.filters.map((f, i) => (i === idx ? { ...f, ...next } : f)));
  };
  const removeFilter = (idx) => {
    patch("filters", draft.filters.filter((_, i) => i !== idx));
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="lg">
      <DialogTitle sx={{ py: 1.75 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Iconify icon="solar:widget-add-linear" width={18} />
          <Typography sx={{ typography: "s1", fontWeight: 700 }}>
            {initial ? "Edit widget" : "New widget"}
          </Typography>
          <Box sx={{ flex: 1 }} />
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify icon="eva:close-fill" width={16} />
          </IconButton>
        </Stack>
      </DialogTitle>

      <DialogContent dividers sx={{ p: 0 }}>
        <Box sx={{
          display: "grid", gridTemplateColumns: { xs: "1fr", md: "360px 1fr" },
          minHeight: 520,
        }}>
          {/* ── form ── */}
          <Box sx={{ p: 2.5, borderRight: { md: "1px solid" }, borderColor: "divider", overflowY: "auto", maxHeight: 640 }}>
            <Stack spacing={2}>
              <Field label="Title">
                <TextField
                  size="small" fullWidth
                  value={draft.title}
                  onChange={(e) => patch("title", e.target.value)}
                  placeholder="e.g. Pass rate by persona"
                />
              </Field>

              <Field label="Data source" hint="Where the numbers come from">
                <TextField
                  size="small" fullWidth select
                  value={draft.source}
                  onChange={(e) => patch("source", e.target.value)}
                >
                  {DATA_SOURCES.map((s) => (
                    <MenuItem key={s.id} value={s.id}>
                      <Stack>
                        <Typography sx={{ fontSize: 13, fontWeight: 600 }}>{s.label}</Typography>
                        <Typography sx={{ fontSize: 11, color: "text.subtitle" }}>{s.hint}</Typography>
                      </Stack>
                    </MenuItem>
                  ))}
                </TextField>
              </Field>

              <Field label="Metric">
                <TextField
                  size="small" fullWidth select
                  value={draft.metric}
                  onChange={(e) => patch("metric", e.target.value)}
                >
                  {eligibleMetrics.map((m) => (
                    <MenuItem key={m.id} value={m.id}>{m.label}</MenuItem>
                  ))}
                </TextField>
              </Field>

              <Field label="Chart type" hint={restrictCharts ? "Some chart types are hidden to keep this widget's meaning intact. Use Customize a copy for full freedom." : undefined}>
                <TextField
                  size="small" fullWidth select
                  value={draft.chart}
                  onChange={(e) => patch("chart", e.target.value)}
                >
                  {CHART_TYPES
                    .filter((c) => !restrictCharts || restrictCharts.includes(c.id))
                    .map((c) => (
                      <MenuItem key={c.id} value={c.id}>
                        <Stack>
                          <Typography sx={{ fontSize: 13, fontWeight: 600 }}>{c.label}</Typography>
                          <Typography sx={{ fontSize: 11, color: "text.subtitle" }}>{c.hint}</Typography>
                        </Stack>
                      </MenuItem>
                    ))}
                </TextField>
              </Field>

              {draft.chart !== "bignumber" && (
                <Field label={isMatrix ? "Rows (Group by)" : "Group by"}>
                  <TextField
                    size="small" fullWidth select
                    value={draft.groupBy || ""}
                    onChange={(e) => patch("groupBy", e.target.value)}
                  >
                    {eligibleGroups.map((g) => (
                      <MenuItem key={g.id} value={g.id}>{g.label}</MenuItem>
                    ))}
                  </TextField>
                </Field>
              )}

              {isMatrix && (
                <Field label="Columns (Split by)" hint="Each row gets segmented by this second dimension">
                  <TextField
                    size="small" fullWidth select
                    value={draft.subGroupBy || ""}
                    onChange={(e) => patch("subGroupBy", e.target.value)}
                  >
                    {eligibleGroups.filter((g) => g.id !== draft.groupBy).map((g) => (
                      <MenuItem key={g.id} value={g.id}>{g.label}</MenuItem>
                    ))}
                  </TextField>
                </Field>
              )}

              {(draft.chart === "bar" || draft.chart === "table" || draft.chart === "heatmap" || draft.chart === "stacked_bar") && (
                <Field label="Show top N">
                  <TextField
                    size="small" fullWidth type="number"
                    inputProps={{ min: 3, max: 30 }}
                    value={draft.limit || 8}
                    onChange={(e) => patch("limit", Math.max(3, Math.min(30, Number(e.target.value) || 8)))}
                  />
                </Field>
              )}

              {/* ── Filters ── */}
              <Box>
                <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
                  <Typography sx={{ fontSize: 10.5, color: "text.subtitle", fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase" }}>
                    Filters
                  </Typography>
                  <Box sx={{ flex: 1 }} />
                  <Button
                    size="small" onClick={addFilter}
                    startIcon={<Iconify icon="solar:add-circle-linear" width={13} />}
                    sx={{ typography: "s3", fontWeight: 700, textTransform: "none", py: 0.25 }}
                  >
                    Add filter
                  </Button>
                </Stack>
                {(!draft.filters || draft.filters.length === 0) && (
                  <Typography sx={{ fontSize: 11.5, color: "text.subtitle" }}>
                    All rows included. Add a filter to narrow the data.
                  </Typography>
                )}
                <Stack spacing={1}>
                  {(draft.filters || []).map((f, i) => (
                    <FilterRow
                      key={i}
                      value={f}
                      ctx={ctx}
                      onChange={(next) => updateFilter(i, next)}
                      onRemove={() => removeFilter(i)}
                    />
                  ))}
                </Stack>
              </Box>
            </Stack>
          </Box>

          {/* ── live preview ── */}
          <Box sx={{ p: 2.5, bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.02 : 0.015), overflowY: "auto" }}>
            <Typography sx={{ fontSize: 10.5, color: "text.subtitle", fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase", mb: 1 }}>
              Live preview
            </Typography>
            <Box sx={{
              border: "1px solid", borderColor: "divider", borderRadius: 1.5,
              bgcolor: "background.paper", overflow: "hidden",
            }}>
              <Box sx={{ px: 3, pt: 2.5, pb: 1.5 }}>
                <Typography sx={{ fontSize: 15, fontWeight: 700, color: "text.primary", letterSpacing: -0.1 }}>
                  {draft.title || "Untitled widget"}
                </Typography>
                {draft.filters?.length > 0 && (
                  <Stack direction="row" spacing={0.5} sx={{ mt: 0.75, flexWrap: "wrap", rowGap: 0.5 }}>
                    {draft.filters.map((f, i) => (
                      <Chip
                        key={i} size="small"
                        label={filterSummary(f)}
                        sx={{ fontSize: 10.5, height: 20 }}
                      />
                    ))}
                  </Stack>
                )}
              </Box>
              <CustomWidgetBody config={draft} ctx={ctx} />
            </Box>
          </Box>
        </Box>
      </DialogContent>

      <DialogActions sx={{ px: 2.5, py: 1.75 }}>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!canSave}
          onClick={() => { onSave(draft); onClose(); }}
          startIcon={<Iconify icon="solar:check-circle-linear" width={15} />}
        >
          {initial ? "Save changes" : "Add widget"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
WidgetEditor.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  ctx: PropTypes.object.isRequired,
  initial: PropTypes.object,
  restrictCharts: PropTypes.arrayOf(PropTypes.string),
};

function pickSubGroupBy(groupBy) {
  const candidates = ["use_case", "persona", "status", "sentiment"].filter((c) => c !== groupBy);
  return candidates[0] || null;
}

function filterSummary(f) {
  const field = FILTER_FIELDS.find((x) => x.id === f.field)?.label || f.field;
  const op = FILTER_OPS.find((x) => x.id === f.op)?.label || f.op;
  const vals = (f.values || []).join(", ") || "…";
  return `${field} ${op} ${vals}`;
}

function Field({ label, hint, children }) {
  return (
    <Box>
      <Typography sx={{ fontSize: 10.5, color: "text.subtitle", fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase", mb: 0.75 }}>
        {label}
      </Typography>
      {children}
      {hint && (
        <Typography sx={{ fontSize: 11, color: "text.subtitle", mt: 0.5 }}>{hint}</Typography>
      )}
    </Box>
  );
}
Field.propTypes = { label: PropTypes.string, hint: PropTypes.string, children: PropTypes.node };

function FilterRow({ value, ctx, onChange, onRemove }) {
  const options = useMemo(() => distinctValues(value.field, ctx), [value.field, ctx]);
  return (
    <Box sx={{
      p: 1, border: "1px solid", borderColor: "divider", borderRadius: 1,
      display: "grid", gridTemplateColumns: "1fr 100px", columnGap: 1, rowGap: 1,
      alignItems: "start",
    }}>
      <TextField
        size="small" select
        value={value.field}
        onChange={(e) => onChange({ field: e.target.value, values: [] })}
        inputProps={{ style: { fontSize: 12.5 } }}
      >
        {FILTER_FIELDS.map((f) => (
          <MenuItem key={f.id} value={f.id}>{f.label}</MenuItem>
        ))}
      </TextField>
      <TextField
        size="small" select
        value={value.op}
        onChange={(e) => onChange({ op: e.target.value })}
        inputProps={{ style: { fontSize: 12.5 } }}
      >
        {FILTER_OPS.map((o) => (
          <MenuItem key={o.id} value={o.id}>{o.label}</MenuItem>
        ))}
      </TextField>
      <Autocomplete
        multiple size="small" fullWidth freeSolo
        options={options}
        value={value.values || []}
        onChange={(_e, next) => onChange({ values: next })}
        renderInput={(params) => <TextField {...params} placeholder="Values…" size="small" />}
        sx={{ gridColumn: "1 / -1" }}
        ChipProps={{ size: "small" }}
      />
      <Box sx={{ gridColumn: "1 / -1", display: "flex", justifyContent: "flex-end" }}>
        <Button size="small" color="error" onClick={onRemove} sx={{ typography: "s3", textTransform: "none" }}>
          Remove filter
        </Button>
      </Box>
    </Box>
  );
}
FilterRow.propTypes = { value: PropTypes.object.isRequired, ctx: PropTypes.object.isRequired, onChange: PropTypes.func.isRequired, onRemove: PropTypes.func.isRequired };
