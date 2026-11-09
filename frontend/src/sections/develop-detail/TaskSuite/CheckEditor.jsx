import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
  Drawer,
  FormControlLabel,
  IconButton,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useForm, useWatch } from "react-hook-form";
import { useQuery } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import { FormCodeEditor } from "src/components/form-code-editor";
import { EvalPickerDrawer } from "src/sections/common/EvalPicker";
import PathInput from "src/sections/common/Values/PathInput";
import { DraftChip } from "src/sections/common/DevelopCellRenderer/CellRenderers/ChecksCellRenderer";
import { useChangeSet } from "../DataTab/Versions/changeSetStore";
import SuiteScopeField from "./SuiteScopeField";
import {
  removeSuiteCheck,
  suiteChangeText,
  suiteChecksOfEnvironment,
  suiteChecksOp,
  takenKeys,
  upsertSuiteCheck,
} from "./suiteChecksModel";
import {
  useDatasetEnvironment,
  useRowCells,
} from "../DataTab/DatapointDrawerV2/itemHooks";
import {
  AGGREGATES,
  CHECK_TYPES,
  PYTHON_SIGNATURE,
  actualText,
  applyToCategory,
  buildCheck,
  checkSentence,
  draftFromCheck,
  evaluateEndState,
  parseChecks,
  parseExpected,
  upsertCheck,
  validateDraft,
} from "./checkTypes";
import { parseJson } from "./itemModel";

const HELP = {
  end_state: "A path into the sandbox's end state, compared with a value",
  tool_call: "Which tools the agent called, how often, with which arguments",
  rubric: "An eval judges the final reply or the whole transcript",
  python: "Your function returns true or false",
  assertion: "One sentence the final reply must hold to, judged on its own",
};
const MONO = { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" };
const MONO_INPUT = { ...MONO, fontSize: 13 };

/** A field label; `tip` is its info tooltip, `hint` one grey line under it. */
const Field = ({ label, children, hint, tip }) => (
  <Box>
    <Stack direction="row" alignItems="center" gap={0.5} sx={{ mb: 0.5 }}>
      <Typography typography="s2" fontWeight={600}>
        {label}
      </Typography>
      {tip && (
        <CustomTooltip show size="small" placement="top" arrow title={tip}>
          <Iconify
            icon="mdi:information-outline"
            width={14}
            sx={{ color: "text.disabled" }}
          />
        </CustomTooltip>
      )}
    </Stack>
    {children}
    {hint && (
      <Typography typography="s3" color="text.secondary" sx={{ mt: 0.5 }}>
        {hint}
      </Typography>
    )}
  </Box>
);
Field.propTypes = {
  label: PropTypes.node,
  children: PropTypes.node,
  hint: PropTypes.node,
  tip: PropTypes.node,
};

/** The row when the editor is opened without one (get-row-data). */
function useRowFallback(datasetId, rowId, enabled) {
  return useQuery({
    queryKey: ["develop", "check-editor-row", datasetId, rowId],
    queryFn: () =>
      axios.post(endpoints.develop.getRowData(datasetId), {
        row_id: rowId,
        filters: [],
        sort: [],
      }),
    select: (res) => res.data?.result?.current ?? null,
    enabled: Boolean(datasetId && rowId) && enabled,
  });
}

/** Every row of the dataset (task suites are small): `{row_id, category, checks}`. */
function useCategoryRows(datasetId, columns, enabled) {
  const catId = columns.find((c) => c.name === "category")?.id;
  const checksId = columns.find((c) => c.name === "checks")?.id;
  return useQuery({
    queryKey: ["develop", "check-editor-rows", datasetId],
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetDetail(datasetId), {
        params: { page_size: 500, current_page_index: 0 },
      }),
    select: (res) =>
      (res.data?.result?.table ?? []).map((r) => ({
        row_id: r.row_id,
        category: r[catId]?.cell_value ?? null,
        checks: r[checksId]?.cell_value ?? "[]",
      })),
    enabled: Boolean(datasetId && catId && checksId) && enabled,
  });
}

/**
 * Check eval editor (R2-SPEC §5.3.8 c): four types — `End state equals`
 * (FA Path + `value | count | exists` + expected, evaluated live on the
 * row's start state), `Tool called / not called`, `Rubric (LLM judge)`
 * (the existing eval picker) and `Python function` (the ExecuteCode
 * editor). Name, one-line description, `Draft` until `Mark reviewed`.
 * `Save` adds a pending change on this row; `Apply to all rows in
 * category …` adds one per row of the category.
 *
 * Props (frozen, R2 manifest row 28): open, onClose, datasetId, rowId.
 * Optional (additive): `checkKey` (edit that check; null = new), `row`.
 */
const CheckEditor = ({
  open,
  onClose,
  datasetId,
  rowId,
  checkKey = null,
  row: rowProp,
}) => {
  const fallback = useRowFallback(datasetId, rowId, open && !rowProp);
  const row = rowProp ?? fallback.data ?? null;
  const cells = useRowCells(datasetId, row);
  const {
    environment,
    saved: savedEnvironment,
    pendingOp: environmentOp,
  } = useDatasetEnvironment(datasetId);
  const changeSet = useChangeSet(datasetId);
  // W6 C1: the suite-wide checks (saved, or with their pending change).
  const suite = suiteChecksOfEnvironment(environment);
  const [scope, setScope] = useState("task");
  const [suiteEditing, setSuiteEditing] = useState(null);
  const checks = useMemo(
    () => parseChecks(cells.byName.checks),
    [cells.byName.checks],
  );
  const existing = checkKey
    ? checks.find((c) => c.key === checkKey) ?? null
    : null;
  const startState = parseJson(cells.byName.start_state);
  const category = cells.byName.category ?? null;
  const orderId = cells.byName.order_id ?? "";
  const toolNames =
    environment?.mcp?.tool_names ?? parseJson(cells.byName.allowed_tools) ?? [];
  const tables =
    environment?.start_state_tables ?? Object.keys(startState ?? {});

  const initial = useMemo(
    () => draftFromCheck(existing, { defaults: { argValue: orderId } }),
    [existing, orderId],
  );
  const [d, setD] = useState(initial);
  const [touched, setTouched] = useState({});
  const [pickerOpen, setPickerOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const { control, reset } = useForm({ defaultValues: { code: initial.code } });
  const code = useWatch({ control, name: "code" });
  useEffect(() => {
    if (!open) return;
    setD(initial);
    setTouched({});
    setScope("task");
    setSuiteEditing(null);
    reset({ code: initial.code });
  }, [open, initial, reset]);
  const draft = { ...d, code: code ?? d.code };
  const set = (patch) => {
    setD((x) => ({ ...x, ...patch }));
    setTouched((t) => ({
      ...t,
      ...Object.fromEntries(Object.keys(patch).map((k) => [k, true])),
    }));
  };

  const otherKeys = takenKeys({
    taskKeys: checks.map((c) => c.key).filter((k) => k !== existing?.key),
    suite,
    editing: suiteEditing,
  });
  const validation = validateDraft(draft, { existingKeys: otherKeys });
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  const built = validation.valid ? buildCheck(draft) : null;
  const sentence = checkSentence(
    built ?? buildCheck({ ...draft, key: draft.key || "…" }),
  );
  const evalOnStart =
    draft.type === "end_state" && draft.path
      ? evaluateEndState(
          {
            path: draft.path,
            aggregate: draft.aggregate,
            expected: parseExpected(draft.expected),
          },
          startState,
        )
      : null;
  const catRows = useCategoryRows(
    datasetId,
    cells.columns,
    open && Boolean(category),
  );
  const siblings = (catRows.data ?? []).filter((r) => r.category === category);

  const close = () => (dirty ? setConfirm(true) : onClose?.());
  const checksCol = cells.columnOf("checks");
  const suiteOp = (next) =>
    suiteChecksOp({
      saved: savedEnvironment,
      pendingOp: environmentOp,
      next,
    });
  const editSuiteCheck = (check) => {
    const next = draftFromCheck(check, { defaults: { argValue: orderId } });
    setD(next);
    reset({ code: next.code });
    setTouched({});
    setScope("suite");
    setSuiteEditing(check.key);
  };
  const removeFromSuite = (key) => {
    changeSet.addOp(suiteOp(removeSuiteCheck(suite, key)));
    if (suiteEditing === key) setSuiteEditing(null);
    enqueueSnackbar(suiteChangeText(key, { removed: true }), {
      variant: "success",
    });
  };
  const save = () => {
    if (built && scope === "suite") {
      const next = upsertSuiteCheck(
        suiteEditing && suiteEditing !== built.key
          ? removeSuiteCheck(suite, suiteEditing)
          : suite,
        built,
      );
      changeSet.addOp(suiteOp(next));
      enqueueSnackbar(suiteChangeText(built.key, { count: next.length }), {
        variant: "success",
      });
      onClose?.();
      return;
    }
    if (!built || !checksCol) return;
    changeSet.addOp({
      op: "update_cell",
      row_id: rowId,
      column_id: checksCol.id,
      value: JSON.stringify(upsertCheck(checks, built)),
      before: cells.original.checks ?? "",
    });
    enqueueSnackbar(
      `${existing ? "Updated" : "Added"} ${built.key} · unsaved`,
      { variant: "success" },
    );
    onClose?.();
  };
  const applyAll = () => {
    if (!built || !checksCol) return;
    // Pending edits of the open row win over the table's copy.
    const rows = siblings.map((r) =>
      r.row_id === rowId ? { ...r, checks: cells.byName.checks } : r,
    );
    const out = applyToCategory(rows, built, category);
    out.forEach((o) =>
      changeSet.addOp({
        op: "update_cell",
        row_id: o.rowId,
        column_id: checksCol.id,
        value: o.value,
        before: o.rowId === rowId ? cells.original.checks ?? "" : o.before,
      }),
    );
    enqueueSnackbar(
      `${built.key} added to ${out.length} rows in ${category} · unsaved`,
      {
        variant: "success",
      },
    );
    onClose?.();
  };

  const err = (field) =>
    (touched[field] || draft[field]) && validation.field === field
      ? validation.reason
      : null;
  const typeMeta = CHECK_TYPES.find((t) => t.id === draft.type);

  return (
    <>
      <Drawer
        anchor="right"
        open={Boolean(open)}
        onClose={close}
        PaperProps={{
          sx: {
            width: 600,
            maxWidth: "100vw",
            display: "flex",
            flexDirection: "column",
          },
        }}
        data-testid="check-editor"
      >
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{
            px: 2.5,
            py: 1.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography typography="m3" fontWeight={600}>
            {existing
              ? `Edit check · ${existing.name ?? existing.key}`
              : "Add check"}
          </Typography>
          {draft.draft && <DraftChip />}
          <Box sx={{ flex: 1 }} />
          {draft.draft && (
            <Button
              size="small"
              variant="text"
              onClick={() => set({ draft: false })}
              data-testid="check-mark-reviewed"
              sx={{ typography: "s2" }}
            >
              Mark reviewed
            </Button>
          )}
          <IconButton size="small" aria-label="Close" onClick={close}>
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
        </Stack>

        <Stack gap={2.25} sx={{ px: 2.5, py: 2, flex: 1, overflowY: "auto" }}>
          <Box
            sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1 }}
            role="radiogroup"
            aria-label="Check type"
          >
            {CHECK_TYPES.map((t) => {
              const on = draft.type === t.id;
              return (
                <CustomTooltip
                  key={t.id}
                  show
                  size="small"
                  placement="top"
                  arrow
                  title={HELP[t.id]}
                >
                  <ButtonBase
                    role="radio"
                    aria-checked={on}
                    data-testid={`check-type-${t.id}`}
                    onClick={() => set({ type: t.id })}
                    sx={{
                      justifyContent: "flex-start",
                      alignItems: "center",
                      gap: 1,
                      px: 1.25,
                      py: 1,
                      textAlign: "left",
                      borderRadius: 1,
                      border: "1px solid",
                      borderColor: on ? "primary.main" : "divider",
                      bgcolor: (th) =>
                        on
                          ? alpha(th.palette.primary.main, 0.08)
                          : "transparent",
                    }}
                  >
                    <Iconify
                      icon={t.icon}
                      width={18}
                      sx={{ color: on ? "primary.main" : "text.secondary" }}
                    />
                    <Typography typography="s2" fontWeight={600}>
                      {t.label}
                    </Typography>
                  </ButtonBase>
                </CustomTooltip>
              );
            })}
          </Box>

          <SuiteScopeField
            scope={scope}
            onScope={setScope}
            suite={suite}
            editingKey={suiteEditing}
            onEdit={editSuiteCheck}
            onRemove={removeFromSuite}
            lockedToTask={Boolean(existing)}
          />

          <Box
            sx={{ display: "grid", gridTemplateColumns: "1fr 1.4fr", gap: 1.5 }}
          >
            <Field label="Name">
              <TextField
                size="small"
                fullWidth
                value={draft.key}
                placeholder="refund_once"
                onChange={(e) => set({ key: e.target.value.trim() })}
                error={Boolean(err("key"))}
                helperText={err("key") ?? " "}
                inputProps={{ "aria-label": "Check name", style: MONO_INPUT }}
              />
            </Field>
            <Field label="Description">
              <TextField
                size="small"
                fullWidth
                value={draft.description}
                placeholder="One line, shown on hover in results"
                onChange={(e) => set({ description: e.target.value })}
                inputProps={{ maxLength: 120, "aria-label": "Description" }}
              />
            </Field>
          </Box>

          {draft.type === "end_state" && (
            <Stack gap={1.5}>
              <Field
                label="Path into the end state"
                hint={`Tables: ${tables.join(", ") || "—"}`}
                tip={
                  'Filter a list with [?(@.key=="value")]; for example refunds[?(@.order_id=="A-8830")].'
                }
              >
                <PathInput
                  value={draft.path}
                  onChange={(value) => set({ path: value })}
                  columns={tables}
                  placeholder={'refunds[?(@.order_id=="A-8830")]'}
                  error={
                    touched.path && !draft.path
                      ? "Enter a path into the end state"
                      : undefined
                  }
                />
              </Field>
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: "160px 1fr",
                  gap: 1.5,
                }}
              >
                <Field label="Compare">
                  <Select
                    size="small"
                    fullWidth
                    value={draft.aggregate}
                    onChange={(e) => set({ aggregate: e.target.value })}
                    inputProps={{ "aria-label": "Aggregate" }}
                  >
                    {AGGREGATES.map((a) => (
                      <MenuItem key={a} value={a} dense>
                        {a}
                      </MenuItem>
                    ))}
                  </Select>
                </Field>
                <Field
                  label={
                    draft.aggregate === "exists" ? "Expect" : "Expected value"
                  }
                >
                  {draft.aggregate === "exists" ? (
                    <ToggleButtonGroup
                      size="small"
                      exclusive
                      value={draft.expected === "false" ? "false" : "true"}
                      onChange={(_, v) => v && set({ expected: v })}
                    >
                      <ToggleButton
                        value="true"
                        sx={{ textTransform: "none", py: 0.5 }}
                      >
                        exists
                      </ToggleButton>
                      <ToggleButton
                        value="false"
                        sx={{ textTransform: "none", py: 0.5 }}
                      >
                        does not exist
                      </ToggleButton>
                    </ToggleButtonGroup>
                  ) : (
                    <TextField
                      size="small"
                      fullWidth
                      value={draft.expected}
                      placeholder={
                        draft.aggregate === "count" ? "1" : '"A-8830" or 149'
                      }
                      onChange={(e) => set({ expected: e.target.value })}
                      error={Boolean(err("expected"))}
                      helperText={err("expected") ?? " "}
                      inputProps={{
                        "aria-label": "Expected value",
                        style: MONO_INPUT,
                      }}
                    />
                  )}
                </Field>
              </Box>
            </Stack>
          )}

          {draft.type === "tool_call" && (
            <Stack gap={1.5}>
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: "1fr auto",
                  gap: 1.5,
                  alignItems: "end",
                }}
              >
                <Field label="Tool">
                  <Select
                    size="small"
                    fullWidth
                    displayEmpty
                    value={draft.tool}
                    onChange={(e) => set({ tool: e.target.value })}
                    renderValue={(v) =>
                      v || (
                        <Typography typography="s2" color="text.disabled">
                          Pick a tool
                        </Typography>
                      )
                    }
                    inputProps={{ "aria-label": "Tool" }}
                    sx={MONO}
                  >
                    {toolNames.map((t) => (
                      <MenuItem key={t} value={t} dense sx={MONO}>
                        {t}
                      </MenuItem>
                    ))}
                  </Select>
                </Field>
                <ToggleButtonGroup
                  size="small"
                  exclusive
                  value={draft.toolMode}
                  onChange={(_, v) => v && set({ toolMode: v })}
                  sx={{ height: 40 }}
                >
                  <ToggleButton value="called" sx={{ textTransform: "none" }}>
                    called
                  </ToggleButton>
                  <ToggleButton
                    value="not_called"
                    sx={{ textTransform: "none" }}
                  >
                    not called
                  </ToggleButton>
                </ToggleButtonGroup>
              </Box>
              {draft.toolMode === "called" && (
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: "130px 80px 1fr 1fr",
                    gap: 1,
                    alignItems: "start",
                  }}
                >
                  <Select
                    size="small"
                    value={draft.timesOp}
                    onChange={(e) => set({ timesOp: e.target.value })}
                    inputProps={{ "aria-label": "Times" }}
                  >
                    <MenuItem value="exactly" dense>
                      exactly
                    </MenuItem>
                    <MenuItem value="at_least" dense>
                      at least
                    </MenuItem>
                    <MenuItem value="at_most" dense>
                      at most
                    </MenuItem>
                  </Select>
                  <TextField
                    size="small"
                    value={draft.timesN}
                    onChange={(e) => set({ timesN: e.target.value })}
                    error={Boolean(err("timesN"))}
                    inputProps={{
                      "aria-label": "Times count",
                      inputMode: "numeric",
                    }}
                    InputProps={{
                      endAdornment: (
                        <Typography typography="s3" color="text.secondary">
                          ×
                        </Typography>
                      ),
                    }}
                  />
                  <TextField
                    size="small"
                    value={draft.argName}
                    placeholder="argument (optional)"
                    onChange={(e) => set({ argName: e.target.value.trim() })}
                    error={Boolean(err("argName"))}
                    helperText={err("argName") ?? " "}
                    inputProps={{
                      "aria-label": "Argument name",
                      style: MONO_INPUT,
                    }}
                  />
                  <TextField
                    size="small"
                    value={draft.argValue}
                    placeholder="value"
                    onChange={(e) => set({ argValue: e.target.value })}
                    inputProps={{
                      "aria-label": "Argument value",
                      style: MONO_INPUT,
                    }}
                  />
                </Box>
              )}
            </Stack>
          )}

          {draft.type === "rubric" && (
            <Stack gap={1.5}>
              <Field
                label="Eval"
                hint="Picked from your evals with the eval picker."
              >
                <Stack direction="row" alignItems="center" gap={1}>
                  {draft.judge ? (
                    <Box
                      component="span"
                      data-testid="rubric-eval"
                      sx={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 0.5,
                        height: 28,
                        px: 1,
                        borderRadius: 0.75,
                        border: "1px solid",
                        borderColor: "divider",
                        typography: "s2",
                      }}
                    >
                      <Iconify icon="mdi:scale-balance" width={14} />
                      {draft.judge}
                      <Box
                        component="span"
                        sx={{
                          typography: "s3",
                          color: "text.secondary",
                          ml: 0.5,
                        }}
                      >
                        Judge
                      </Box>
                    </Box>
                  ) : null}
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => setPickerOpen(true)}
                    data-testid="rubric-pick"
                  >
                    {draft.judge ? "Change" : "Pick an eval"}
                  </Button>
                </Stack>
              </Field>
              <Field label="Score">
                <RadioGroup
                  row
                  value={draft.scope}
                  onChange={(e) => set({ scope: e.target.value })}
                >
                  <FormControlLabel
                    value="final_reply"
                    control={<Radio size="small" />}
                    label={
                      <Typography typography="s2">The final reply</Typography>
                    }
                  />
                  <FormControlLabel
                    value="transcript"
                    control={<Radio size="small" />}
                    label={
                      <Typography typography="s2">
                        The whole transcript
                      </Typography>
                    }
                  />
                </RadioGroup>
              </Field>
            </Stack>
          )}

          {draft.type === "assertion" && (
            <Field
              label="Assertion"
              hint="Judged by the eval judge with this sentence as its criteria; one sentence per check, each with its own cell and reason."
            >
              <TextField
                size="small"
                fullWidth
                multiline
                minRows={2}
                value={draft.sentence}
                placeholder="The reply never promises a refund date."
                onChange={(e) => set({ sentence: e.target.value })}
                error={Boolean(err("sentence"))}
                helperText={err("sentence") ?? " "}
                inputProps={{
                  "aria-label": "Assertion",
                  "data-testid": "check-assertion",
                }}
              />
            </Field>
          )}

          {draft.type === "python" && (
            <Field
              label="Function"
              hint={
                <Box component="span" sx={MONO}>
                  {PYTHON_SIGNATURE}
                </Box>
              }
              tip="Runs in the sandbox after the attempt; return True to pass."
            >
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: err("code") ? "error.main" : "divider",
                  borderRadius: 1,
                  overflow: "hidden",
                }}
              >
                <FormCodeEditor
                  height="220px"
                  defaultLanguage="python"
                  control={control}
                  fieldName="code"
                  options={{
                    minimap: { enabled: false },
                    automaticLayout: true,
                    wordWrap: "on",
                    scrollBeyondLastLine: false,
                  }}
                />
              </Box>
              {err("code") && (
                <Typography typography="s3" color="error.main" sx={{ mt: 0.5 }}>
                  {err("code")}
                </Typography>
              )}
            </Field>
          )}

          <Box
            data-testid="check-preview"
            sx={{
              p: 1.25,
              borderRadius: 1,
              bgcolor: "background.neutral",
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography typography="s3" color="text.secondary" sx={{ mb: 0.5 }}>
              Preview · {typeMeta?.label}
            </Typography>
            <Typography
              typography="s2"
              sx={{ ...MONO, wordBreak: "break-word" }}
            >
              {sentence} → {draft.key || "…"}
            </Typography>
            {evalOnStart && (
              <Typography
                typography="s3"
                color={evalOnStart.ok ? "text.secondary" : "error.main"}
                sx={{ mt: 0.25 }}
                data-testid="check-preview-start"
              >
                {evalOnStart.ok
                  ? `(on the start state: ${actualText(evalOnStart)})`
                  : evalOnStart.error}
              </Typography>
            )}
          </Box>
        </Stack>

        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{
            px: 2.5,
            py: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {category && scope === "task" && (
            <CustomTooltip
              show
              size="small"
              placement="top"
              arrow
              title={
                validation.valid
                  ? `Adds this check to the ${siblings.length || "…"} rows in ${category} as unsaved changes`
                  : validation.reason
              }
            >
              <span>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={!validation.valid || !siblings.length}
                  onClick={applyAll}
                  data-testid="check-apply-category"
                  sx={{ typography: "s2" }}
                >
                  Apply to all rows in category {category}
                </Button>
              </span>
            </CustomTooltip>
          )}
          <Box sx={{ flex: 1 }} />
          <Button size="small" variant="text" onClick={close}>
            Cancel
          </Button>
          <CustomTooltip
            show={!validation.valid}
            size="small"
            placement="top"
            arrow
            title={validation.reason ?? ""}
          >
            <span>
              <Button
                size="small"
                variant="contained"
                disabled={!validation.valid}
                onClick={save}
                data-testid="check-save"
              >
                Save
              </Button>
            </span>
          </CustomTooltip>
        </Stack>
      </Drawer>
      {pickerOpen && (
        <EvalPickerDrawer
          open={pickerOpen}
          onClose={() => setPickerOpen(false)}
          source="dataset"
          sourceId={datasetId}
          skipConfig
          onEvalAdded={(ev) => {
            set({
              judge:
                ev?.name ??
                ev?.evalTemplateName ??
                ev?.template_name ??
                "Judge",
              judgeId: ev?.templateId ?? ev?.evalId ?? ev?.id ?? null,
            });
            setPickerOpen(false);
          }}
        />
      )}
      <ConfirmDialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Discard this check?"
        content={
          <Typography typography="s1" color="text.secondary">
            Your edits to the check are not saved.
          </Typography>
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => {
              setConfirm(false);
              onClose?.();
            }}
          >
            Discard
          </Button>
        }
      />
    </>
  );
};

CheckEditor.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  datasetId: PropTypes.string,
  rowId: PropTypes.string,
  checkKey: PropTypes.string,
  row: PropTypes.object,
};

export default CheckEditor;
