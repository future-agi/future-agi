import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Divider,
  ListItemText,
  MenuItem,
  MenuList,
  Popover,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { fmtCount } from "src/sections/common/format/fmt";
import ResultsExpressionDialog from "src/sections/common/filters/ResultsExpressionDialog";
import {
  FILTER_ENTRIES,
  NO_SPLIT,
  OPS,
  defaultForm,
  entryDisabledReason,
  formComplete,
  pillFromForm,
  runOptions,
} from "./filterForm";

const Field = ({ label, children }) => (
  <Stack spacing={0.5}>
    <Typography typography="s2" color="text.secondary">
      {label}
    </Typography>
    {children}
  </Stack>
);
Field.propTypes = { label: PropTypes.string, children: PropTypes.node };

const selectSx = { typography: "s1", "& .MuiSelect-select": { py: 0.75 } };

/**
 * `Filter` button + menu (PRODUCT-SPEC §3.10.5). Left: the filter types;
 * right: Metric, Run (`Any candidate` by default) and Op / Value. Applying
 * adds one pill; pills AND together and filter server-side.
 */
const FilterMenu = ({ view, onAdd, compact = false }) => {
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [entryId, setEntryId] = useState(null);
  const [form, setForm] = useState({});
  const entry = FILTER_ENTRIES.find((e) => e.id === entryId) ?? null;
  // integration S7: Filter ▾ › Expression… (shown where the view names its dataset).
  const [exprOpen, setExprOpen] = useState(false);

  const pick = (e) => {
    setEntryId(e.id);
    setForm(defaultForm(e, view));
  };
  const set = (k) => (ev) => setForm((f) => ({ ...f, [k]: ev.target.value }));
  const close = () => {
    setOpen(false);
    setEntryId(null);
  };
  const apply = () => {
    const pill = pillFromForm(entry, form);
    if (pill) onAdd(pill);
    close();
  };

  const runs = useMemo(
    () =>
      runOptions(view.runs, view.baselineId, {
        candidatesOnly: !!entry?.compare,
      }),
    [view.runs, view.baselineId, entry],
  );
  const metricOptions =
    entry?.id === "score"
      ? view.metrics.filter((m) => m.output_type === "score")
      : view.metrics;
  const metaValues = view.metadataValues?.[form.metaKey] ?? [];

  const renderField = (f) => {
    switch (f) {
      case "metric":
        return (
          <Field key={f} label="Metric">
            <Select
              size="small"
              value={form.metric ?? ""}
              onChange={set("metric")}
              sx={selectSx}
            >
              {metricOptions.map((m) => (
                <MenuItem key={m.key} value={m.key}>
                  {m.name}
                </MenuItem>
              ))}
            </Select>
          </Field>
        );
      case "run":
        return (
          <Field key={f} label="Run">
            <Select
              size="small"
              value={form.run ?? ""}
              onChange={set("run")}
              sx={selectSx}
            >
              {runs.map((o) => (
                <MenuItem key={o.value} value={o.value}>
                  {o.label}
                </MenuItem>
              ))}
            </Select>
          </Field>
        );
      case "op":
        return (
          <Field key={f} label="Condition">
            <Select
              size="small"
              value={form.op ?? "lt"}
              onChange={set("op")}
              sx={selectSx}
            >
              {OPS.map((o) => (
                <MenuItem key={o.value} value={o.value}>
                  {o.label}
                </MenuItem>
              ))}
            </Select>
          </Field>
        );
      case "value":
        return (
          <Field
            key={f}
            label={
              entry?.id === "ops"
                ? form.opsKind === "cost"
                  ? "Value (USD per row)"
                  : "Value (seconds)"
                : "Value"
            }
          >
            <TextField
              size="small"
              value={form.value ?? ""}
              onChange={set("value")}
              inputProps={{ inputMode: "decimal", "aria-label": "Value" }}
            />
          </Field>
        );
      case "splits":
        return (
          <Field key={f} label="Splits">
            <Stack sx={{ maxHeight: 180, overflow: "auto" }}>
              {[...view.splits, { name: NO_SPLIT, label: "No split" }].map(
                (s) => {
                  const checked = form.splits?.includes(s.name);
                  return (
                    <MenuItem
                      key={s.name}
                      dense
                      onClick={() =>
                        setForm((prev) => ({
                          ...prev,
                          splits: checked
                            ? prev.splits.filter((x) => x !== s.name)
                            : [...(prev.splits ?? []), s.name],
                        }))
                      }
                    >
                      <Checkbox
                        size="small"
                        checked={!!checked}
                        sx={{ p: 0.5, mr: 1 }}
                      />
                      <ListItemText
                        primary={s.label ?? s.name}
                        secondary={
                          s.row_count != null ? `${s.row_count} rows` : null
                        }
                        primaryTypographyProps={{ typography: "s1" }}
                        secondaryTypographyProps={{ typography: "s3" }}
                      />
                    </MenuItem>
                  );
                },
              )}
            </Stack>
          </Field>
        );
      case "metaKey":
        return (
          <Field key={f} label="Field">
            <Select
              size="small"
              value={form.metaKey ?? ""}
              onChange={(e) =>
                setForm((p) => ({
                  ...p,
                  metaKey: e.target.value,
                  metaValue: "",
                }))
              }
              sx={selectSx}
            >
              {view.metadataKeys.map((k) => (
                <MenuItem key={k} value={k}>
                  {k}
                </MenuItem>
              ))}
            </Select>
          </Field>
        );
      case "metaValue":
        return (
          <Field key={f} label="Is">
            <Select
              size="small"
              displayEmpty
              value={form.metaValue ?? ""}
              onChange={set("metaValue")}
              sx={selectSx}
            >
              <MenuItem value="" disabled>
                Pick a value
              </MenuItem>
              {metaValues.map((v) => (
                <MenuItem key={v} value={v}>
                  {v}
                </MenuItem>
              ))}
            </Select>
          </Field>
        );
      case "humanLabel":
        return (
          <Field key={f} label="Label">
            <Select
              size="small"
              value={form.humanLabel ?? ""}
              onChange={set("humanLabel")}
              sx={selectSx}
            >
              {view.humanMetrics.map((m) => (
                <MenuItem key={m.key} value={m.key}>
                  {m.name}
                </MenuItem>
              ))}
            </Select>
          </Field>
        );
      case "prefValue":
        return (
          <Field key={f} label="Preferred">
            <Select
              size="small"
              value={form.prefValue ?? ""}
              onChange={set("prefValue")}
              sx={selectSx}
            >
              {view.runs.map((r) => (
                <MenuItem key={r.run_id} value={r.run_id}>
                  {`${r.label} better`}
                </MenuItem>
              ))}
              <MenuItem value="tie">Tie</MenuItem>
              <MenuItem value="disagree">
                LLM judge disagrees with reviewers
              </MenuItem>
            </Select>
          </Field>
        );
      case "prefSource":
        return form.prefValue === "disagree" ? null : (
          <Field key={f} label="Source">
            <Select
              size="small"
              value={form.prefSource}
              onChange={set("prefSource")}
              sx={selectSx}
            >
              <MenuItem value="human">Reviewers</MenuItem>
              <MenuItem value="judge">LLM judge</MenuItem>
            </Select>
          </Field>
        );
      case "opsKind":
        return (
          <Field key={f} label="Measure">
            <Select
              size="small"
              value={form.opsKind}
              onChange={(e) =>
                setForm((p) => ({
                  ...p,
                  opsKind: e.target.value,
                  value: e.target.value === "cost" ? "0.01" : "3",
                }))
              }
              sx={selectSx}
            >
              <MenuItem value="latency">Latency</MenuItem>
              <MenuItem value="cost">Cost</MenuItem>
            </Select>
          </Field>
        );
      default:
        return null;
    }
  };

  return (
    <>
      <CustomTooltip
        show={compact}
        size="small"
        placement="top"
        arrow
        title="Filter rows by score, split, metadata, cost or latency"
      >
        <Button
          ref={anchorRef}
          variant="outlined"
          size="small"
          aria-label="Filter"
          onClick={() => setOpen(true)}
          startIcon={
            <Iconify icon="material-symbols:filter-list-rounded" width={18} />
          }
          sx={{
            height: 32,
            px: 1.25,
            typography: "s2",
            fontWeight: 500,
            color: "text.primary",
            borderColor: "divider",
            // Compact (below 1440 px): icon only, so the toolbar stays one
            // row and the grid keeps its height.
            ...(compact && {
              minWidth: 0,
              px: 0.875,
              "& .MuiButton-startIcon": { mx: 0 },
            }),
          }}
        >
          {compact ? null : "Filter"}
        </Button>
      </CustomTooltip>
      <Popover
        open={open}
        anchorEl={anchorRef.current}
        onClose={close}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: { sx: { mt: 0.5, borderRadius: 1, width: entry ? 560 : 260 } },
        }}
      >
        <Stack direction="row" sx={{ minHeight: 200 }}>
          <MenuList dense sx={{ width: 260, py: 0.5, flexShrink: 0 }}>
            {/* R4 lane X: one-click voice filters (rows where any run in
                view matches; the count is the rows the filter keeps). */}
            {(view.quick ?? []).map((q) => (
              <MenuItem
                key={q.id}
                onClick={() => {
                  onAdd(q.pill);
                  close();
                }}
                sx={{ typography: "s1", py: 0.75, gap: 1 }}
                data-testid={`voice-filter-${q.id}`}
              >
                <Box component="span" sx={{ flex: 1, minWidth: 0 }}>
                  {q.label}
                </Box>
                <Typography
                  component="span"
                  typography="s2"
                  color="text.secondary"
                  sx={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {fmtCount(q.count)}
                </Typography>
              </MenuItem>
            ))}
            {view.quick?.length ? <Divider sx={{ my: 0.5 }} /> : null}
            {view.datasetId && (
              <MenuItem
                onClick={() => {
                  close();
                  setExprOpen(true);
                }}
                sx={{ typography: "s1", py: 0.75, gap: 1 }}
                data-testid="filter-expression"
              >
                <Iconify icon="mdi:code-parentheses" width={16} />
                Expression…
              </MenuItem>
            )}
            {FILTER_ENTRIES.map((e) => {
              const reason = entryDisabledReason(e, view);
              const item = (
                <MenuItem
                  key={e.id}
                  selected={e.id === entryId}
                  disabled={!!reason}
                  onClick={() => pick(e)}
                  sx={{ typography: "s1", py: 0.75 }}
                >
                  {e.label}
                </MenuItem>
              );
              return reason ? (
                <CustomTooltip
                  key={e.id}
                  show
                  size="small"
                  placement="right"
                  arrow
                  title={reason}
                >
                  <span>{item}</span>
                </CustomTooltip>
              ) : (
                item
              );
            })}
          </MenuList>
          {entry && (
            <>
              <Divider orientation="vertical" flexItem />
              <Stack spacing={1.5} sx={{ p: 2, flex: 1, minWidth: 0 }}>
                <Typography typography="s1" fontWeight="fontWeightSemiBold">
                  {entry.label.replace("…", "")}
                </Typography>
                {entry.fields.map(renderField)}
                <Box sx={{ flex: 1 }} />
                <Stack direction="row" justifyContent="flex-end" gap={1}>
                  <Button size="small" onClick={close}>
                    Cancel
                  </Button>
                  <Button
                    size="small"
                    variant="contained"
                    color="primary"
                    disabled={!formComplete(entry, form)}
                    onClick={apply}
                  >
                    Add filter
                  </Button>
                </Stack>
              </Stack>
            </>
          )}
        </Stack>
      </Popover>
      {view.datasetId && (
        <ResultsExpressionDialog
          open={exprOpen}
          onClose={() => setExprOpen(false)}
          datasetId={view.datasetId}
          view={view}
          onApply={(pill) => {
            onAdd(pill);
            setExprOpen(false);
          }}
        />
      )}
    </>
  );
};

FilterMenu.propTypes = {
  compact: PropTypes.bool,
  view: PropTypes.shape({
    datasetId: PropTypes.string,
    resultsView: PropTypes.object,
    runs: PropTypes.array,
    runCount: PropTypes.number,
    baselineId: PropTypes.string,
    metrics: PropTypes.array,
    humanMetrics: PropTypes.array,
    splits: PropTypes.array,
    metadataKeys: PropTypes.array,
    metadataValues: PropTypes.object,
    hasTrials: PropTypes.bool,
    hasPreference: PropTypes.bool,
    hasAgent: PropTypes.bool,
    candidateId: PropTypes.string,
    quick: PropTypes.arrayOf(
      PropTypes.shape({
        id: PropTypes.string,
        label: PropTypes.string,
        count: PropTypes.number,
        pill: PropTypes.object,
      }),
    ),
  }).isRequired,
  onAdd: PropTypes.func.isRequired,
};

export default FilterMenu;
