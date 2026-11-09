import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  DialogContent,
  FormControlLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar, closeSnackbar } from "notistack";
import {
  datasetVersionsKey,
  latestOf,
  useDatasetVersions,
} from "src/api/develop/dataset-versions";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import CustomDialog from "src/sections/develop-detail/Common/CustomDialog/CustomDialog";
import { SplitChip } from "src/sections/common/Datasets/SplitChips";
import {
  useDevelopDatasetList,
  useGetDatasetColumns,
} from "src/api/develop/develop-detail";
import { useAddResultsToDataset } from "src/api/develop/run-results";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import { ProductionWarningChip } from "src/sections/common/AddData/ExpectedSourceControl";
import {
  SUGGESTED_SPLITS,
  columnMapCount,
  columnMapText,
  defaultColumnMap,
  noColumnsMapReason,
  defaultDescription,
  dialogTitleFor,
  expectedPlanOf,
  crossMappedColumns,
  crossMapText,
  latestExpectedOf,
  noOpReason,
  nothingToReplaceText,
  r6Body,
  readsRunOutput,
  replaceExpectedText,
  splitChipLabel,
  splitMembership,
  sourceKind,
  sourceRunFor,
  successText,
  trialOutputOf,
} from "./addToDatasetModel";

const Label = ({ children }) => (
  <Typography typography="s2" color="text.secondary" sx={{ mb: 0.5 }}>
    {children}
  </Typography>
);
Label.propTypes = { children: PropTypes.node };

/**
 * Add result rows to a dataset (PRODUCT-SPEC §3.10.8, C11): this dataset
 * (split membership and optionally Expected from the run's output, as one
 * version) or another / a new dataset (rows copied with their source).
 */
const clip = (v, n = 70) => {
  const raw =
    v == null
      ? ""
      : typeof v === "string"
        ? v
        : v.primary ?? v.value ?? v.text ?? JSON.stringify(v);
  const t = String(typeof raw === "string" ? raw : JSON.stringify(raw))
    .replace(/\s+/g, " ")
    .trim();
  return t ? (t.length > n ? `${t.slice(0, n - 1)}…` : t) : "—";
};

/**
 * IT-R4 G3 (P5): before → after for the first 3 rows whose expected value
 * is replaced by the run's output. integration review (DI-06 d): the after is the
 * picked try's output (the text the save writes), not always try 1.
 * integration review 2: the before is the expected as it stands now (the latest
 * version), and only rows the save changes are listed; rows that already
 * hold the output, or have no such try, are said once below.
 */
const rowNames = (list) =>
  list.map((r) => r.row_label ?? `Row ${r.row_order ?? ""}`).join(", ");

function ExpectedPreview({ plan, run, trial = 1, tries = 1 }) {
  if (!plan || !run) return null;
  const changing = [...plan.change, ...plan.unsure];
  const list = changing.filter((r) => r.results).slice(0, 3);
  const tryText = tries > 1 ? `try ${trial} ` : "";
  const notes = [
    plan.same.length
      ? `${plan.same.length === 1 ? `${rowNames(plan.same)} already holds` : `${plan.same.length} rows (${rowNames(plan.same.slice(0, 3))}${plan.same.length > 3 ? ", …" : ""}) already hold`} this output: left as is.`
      : null,
    plan.none.length
      ? `${plan.none.length === 1 ? rowNames(plan.none) : `${plan.none.length} rows`} ${plan.none.length === 1 ? "has" : "have"} no ${tryText}output: left as is.`
      : null,
  ].filter(Boolean);
  if (!list.length && !notes.length) return null;
  return (
    <Stack
      spacing={0.75}
      data-testid="add-to-dataset-expected-preview"
      sx={{
        mt: 1,
        p: 1,
        borderRadius: 0.75,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography typography="s3" color="text.secondary" fontWeight={600}>
        {`Expected now → ${run.label}'s ${tryText}output${changing.length > list.length && list.length ? ` (first ${list.length})` : ""}`}
      </Typography>
      {list.map((r) => (
        <Box key={r.example_id} sx={{ minWidth: 0 }}>
          <Typography typography="s3" fontWeight={600}>
            {r.row_label ?? `Row ${r.row_order ?? ""}`}
          </Typography>
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{ textDecoration: "line-through" }}
            noWrap
          >
            {clip(latestExpectedOf(r))}
          </Typography>
          <Typography typography="s3" noWrap>
            {clip(trialOutputOf(r.results?.[run.run_id], trial))}
          </Typography>
        </Box>
      ))}
      {notes.map((t) => (
        <Typography
          key={t}
          typography="s3"
          color="text.secondary"
          data-testid="add-to-dataset-expected-unchanged"
        >
          {t}
        </Typography>
      ))}
    </Stack>
  );
}

ExpectedPreview.propTypes = {
  plan: PropTypes.object,
  run: PropTypes.object,
  trial: PropTypes.number,
  tries: PropTypes.number,
};

/**
 * integration (DI-06 b): another dataset's Columns step — one line per source
 * user column, each a pick of the target's user columns or Skip, and
 * `N of M columns map`.
 */
function ColumnsStep({ sourceCols, targetCols, map, onChange, count }) {
  return (
    <Box
      data-testid="add-to-dataset-columns"
      sx={{
        mt: 1,
        p: 1,
        borderRadius: 0.75,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 0.75 }}
      >
        <Typography typography="s2" fontWeight={600}>
          Columns
        </Typography>
        <Typography
          typography="s3"
          color="text.secondary"
          data-testid="add-to-dataset-columns-count"
        >
          {columnMapText(count)}
        </Typography>
      </Stack>
      <Stack spacing={0.75}>
        {sourceCols.map((c) => (
          <Stack
            key={c.id}
            direction="row"
            alignItems="center"
            gap={1}
            data-testid={`add-to-dataset-column-${c.name}`}
          >
            <Typography
              typography="s2"
              noWrap
              title={c.name}
              sx={{ flex: 1, minWidth: 0 }}
            >
              {c.name}
            </Typography>
            <Iconify icon="mdi:arrow-right" width={14} />
            <Select
              size="small"
              displayEmpty
              value={map?.[c.id] ?? ""}
              onChange={(e) => onChange(c.id, e.target.value)}
              inputProps={{ "aria-label": `Target column for ${c.name}` }}
              sx={{ flex: 1, minWidth: 0, typography: "s2" }}
            >
              <MenuItem value="">Skip</MenuItem>
              {targetCols.map((t) => (
                <MenuItem key={t.id} value={t.id}>
                  {t.name}
                </MenuItem>
              ))}
            </Select>
          </Stack>
        ))}
      </Stack>
      {crossMappedColumns(sourceCols, targetCols, map).length > 0 && (
        <Alert
          severity="warning"
          sx={{ mt: 1, typography: "s2" }}
          data-testid="add-to-dataset-columns-cross"
        >
          {crossMapText(crossMappedColumns(sourceCols, targetCols, map))}
        </Alert>
      )}
    </Box>
  );
}

ColumnsStep.propTypes = {
  sourceCols: PropTypes.array.isRequired,
  targetCols: PropTypes.array.isRequired,
  map: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  count: PropTypes.object.isRequired,
};

const AddResultsToDatasetDialog = ({
  open,
  onClose,
  datasetId,
  datasetName,
  exampleIds,
  rows,
  runs,
  baselineId,
  pills,
  focusedRunId,
  splits,
}) => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [target, setTarget] = useState("same");
  const [otherId, setOtherId] = useState("");
  const [newName, setNewName] = useState("");
  const [runId, setRunId] = useState(null);
  const [splitIds, setSplitIds] = useState([]);
  const [newSplits, setNewSplits] = useState([]);
  const [splitDraft, setSplitDraft] = useState("");
  const [updateExpected, setUpdateExpected] = useState(false);
  const [expectedFrom, setExpectedFrom] = useState("keep");
  const [description, setDescription] = useState("");
  const [touchedDescription, setTouchedDescription] = useState(false);
  // integration (DI-06): the try whose output is read, the Columns step's map
  // (another dataset) and the server's sentence under the name field.
  const [trial, setTrial] = useState(1);
  const [columnMap, setColumnMap] = useState({});
  const [nameError, setNameError] = useState("");
  // integration review (DI-06 a): the server's sentence for any other refusal,
  // said in the dialog — the toast column hides while a dialog covers it
  // (toastPlacement MIN_ROOM_PX), so the dialog is where it is read.
  const [serverError, setServerError] = useState("");
  // R4 lane X (8f): the dialog opens on the split field (`A` → type `hard`
  // → Enter adds it → Enter again saves).
  const splitInputRef = useRef(null);

  const { data: datasets = [] } = useDevelopDatasetList(undefined, undefined, {
    enabled: open,
  });
  const others = datasets.filter((d) => d.datasetId !== datasetId);
  // N1 versions: the button names the version the save creates.
  const { data: sameVersions } = useDatasetVersions(datasetId, {
    enabled: open && Boolean(datasetId),
  });
  const { data: otherVersions } = useDatasetVersions(otherId || null, {
    enabled: open && target === "other" && Boolean(otherId),
  });
  const run = runs.find((r) => r.run_id === runId);
  const tries = Math.max(1, Number(run?.trials) || 1);
  const kind = sourceKind(pills, runId);
  // integration (DI-06 b): user columns of the source and the chosen target.
  const { data: sourceCols = [], isFetched: sourceColsFetched } =
    useGetDatasetColumns(datasetId, {
      enabled: open && target === "other",
    });
  const { data: targetCols = [], isFetched: targetColsFetched } =
    useGetDatasetColumns(otherId || null, {
      enabled: open && target === "other" && Boolean(otherId),
    });
  const sourceColKey = sourceCols.map((c) => c.id).join();
  const targetColKey = targetCols.map((c) => c.id).join();
  useEffect(() => {
    if (target !== "other" || !otherId) return;
    setColumnMap(defaultColumnMap(sourceCols, targetCols));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, otherId, sourceColKey, targetColKey]);
  const mapCount = columnMapCount(sourceCols, columnMap);
  const colsReady = Boolean(otherId) && sourceColsFetched && targetColsFetched;
  const splitNames = [
    ...splits.filter((s) => splitIds.includes(s.id)).map((s) => s.name),
    ...newSplits,
  ];

  // Reset on open: source run, the split a regression set usually lands in.
  useEffect(() => {
    if (!open) return;
    setTarget("same");
    setOtherId("");
    setNewName("");
    setRunId(sourceRunFor({ pills, runs, baselineId, focusedRunId }));
    const hard = splits.find((s) => s.name === "hard");
    const movement = (pills ?? []).some((p) => p.type === "movement");
    setSplitIds(movement && hard ? [hard.id] : []);
    setNewSplits([]);
    setSplitDraft("");
    setUpdateExpected(false);
    setExpectedFrom("keep");
    setTouchedDescription(false);
    setTrial(1);
    setColumnMap({});
    setNameError("");
    setServerError("");
    const t = setTimeout(() => splitInputRef.current?.focus(), 120);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // A refusal is about the choice it was sent with: a new target or run
  // clears it.
  useEffect(() => {
    setServerError("");
  }, [target, otherId, runId]);

  // integration review (DI-06 d): the try picker and `trial` only when the action
  // reads the run's output (never on a tag-only or keep-expected save).
  const readsOutput = readsRunOutput({ target, updateExpected, expectedFrom });
  const pickedTrial = readsOutput ? Math.min(trial, tries) : 1;
  // integration review 2 (DI-06 d): which picked rows the expected update really
  // changes (the latest expected vs the picked try), said before the click.
  const expectedPlan = useMemo(
    () =>
      target === "same" && updateExpected && runId
        ? expectedPlanOf({ rows, exampleIds, runId, trial: pickedTrial })
        : null,
    [target, updateExpected, runId, rows, exampleIds, pickedTrial],
  );
  const expectedChanges = !expectedPlan || expectedPlan.upTo > 0;
  // The note never claims a change that is not there: an expected-only
  // update names the rows it changes.
  const expectedOnly =
    target === "same" && updateExpected && !splitNames.length && expectedPlan;
  const noteCount = expectedOnly ? expectedPlan.upTo : exampleIds.length;
  const noteUpdates = updateExpected && expectedChanges;
  const autoDescription = useMemo(
    () =>
      defaultDescription({
        count: noteCount,
        runLabel: run?.label,
        kind,
        splitNames,
        target,
        updateExpected: noteUpdates,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [noteCount, run?.label, kind, splitNames.join(), target, noteUpdates],
  );
  useEffect(() => {
    if (!touchedDescription) setDescription(autoDescription);
  }, [autoDescription, touchedDescription]);

  const targetName =
    target === "same"
      ? datasetName
      : target === "new"
        ? newName.trim()
        : others.find((d) => d.datasetId === otherId)?.name;

  // P6: what each split already holds, so a no-op is said before the click.
  const membership = useMemo(
    () => splitMembership(rows, splits),
    [rows, splits],
  );
  const [savingPlain, setSavingPlain] = useState(false);
  const add = useAddResultsToDataset(datasetId, {
    onSuccess: (res, vars) => {
      const result = res?.data?.result ?? {};
      const plain = Boolean(vars?.save_version);
      const destId =
        target === "same" ? datasetId : result.dataset_id ?? otherId;
      const text = successText(result, {
        splitNames: plain ? [] : splitNames,
        target,
        datasetName: result.dataset_name ?? targetName,
        runLabel: run?.label,
        updateExpected: !plain && updateExpected,
      });
      const changed =
        Boolean(result.version?.number) ||
        (result.rows_added ?? 0) > 0 ||
        (result.split_changes ?? 0) > 0;
      const params = new URLSearchParams({ tab: "data" });
      if (result.version?.number) params.set("version", result.version.number);
      if (target === "same" && splitNames.length && !plain)
        params.set("splits", splitNames.join(","));
      // P7: the new version and its splits show in place (no reload).
      queryClient.invalidateQueries({ queryKey: datasetVersionsKey(destId) });
      queryClient.invalidateQueries({ queryKey: ["develop"] });
      setSavingPlain(false);
      enqueueSnackbar(text, {
        key: "add-results-to-dataset",
        // P6: never a success tick on a save that changed nothing.
        variant: changed ? "success" : "info",
        autoHideDuration: 6000,
        action: (key) => (
          <Button
            size="small"
            color="inherit"
            onClick={() => {
              closeSnackbar(key);
              navigate(`/dashboard/develop/${destId}?${params.toString()}`);
            }}
            sx={{ fontWeight: 600 }}
          >
            {result.version?.number ? `Open v${result.version.number}` : "Open"}
          </Button>
        ),
      });
      onClose(true);
    },
    onError: (error) => {
      setSavingPlain(false);
      // integration (DI-06 a): every failure says the server's sentence; a name
      // the server refuses is also said under the name field.
      const text = getRequestErrorMessage(error, "Could not add the rows");
      if (target === "new" && /name/i.test(text)) setNameError(text);
      else setServerError(text);
      enqueueSnackbar(text, { variant: "error" });
    },
  });
  const noOp =
    target === "same"
      ? noOpReason({
          count: exampleIds.length,
          splitIds,
          splitNames,
          newSplits,
          updateExpected: updateExpected && expectedChanges,
          membership,
        }) ??
        (updateExpected && !expectedChanges && !splitNames.length
          ? nothingToReplaceText(expectedPlan, run?.label, pickedTrial)
          : null)
      : null;

  const ready =
    exampleIds.length > 0 &&
    !!runId &&
    !noOp &&
    (target === "same"
      ? splitNames.length > 0 || updateExpected
      : target === "other"
        ? colsReady && mapCount.mapped > 0
        : !!newName.trim());

  // R2 (P5): `…'s reply as expected · needs review` sends the output as
  // expected but marks where it came from (additive `expected_source`).
  const reviewReply = target !== "same" && expectedFrom === "output_review";
  const submit = ({ plain = false } = {}) => {
    setServerError("");
    return add.mutate({
      targetId:
        target === "same" ? datasetId : target === "new" ? "new" : otherId,
      ...r6Body({
        datasetId,
        runId,
        exampleIds,
        target,
        splitIds: plain ? [] : splitIds,
        newSplits: plain ? [] : newSplits,
        updateExpected: plain ? false : updateExpected,
        expectedFrom: reviewReply ? "output" : expectedFrom,
        description:
          plain && !touchedDescription
            ? `Saved from ${run?.label ?? "results"} review (${exampleIds.length} ${exampleIds.length === 1 ? "row" : "rows"} picked)`
            : description,
        newDatasetName: newName.trim(),
        saveVersion: plain,
        trial: plain ? 1 : pickedTrial,
        columnMap: target === "other" ? columnMap : null,
        needsReview: reviewReply,
      }),
    });
  };

  const addDraftSplit = () => {
    const name = splitDraft.trim();
    if (!name) return;
    const existing = splits.find((s) => s.name === name);
    if (existing) setSplitIds((ids) => [...new Set([...ids, existing.id])]);
    else if (!newSplits.includes(name)) setNewSplits((s) => [...s, name]);
    setSplitDraft("");
  };

  const n = exampleIds.length;
  // `Tag 3 rows as hard and save v3` / `Add 3 rows to Golden and save v4`.
  const nextVersion = (id) => {
    const list = id === datasetId ? sameVersions : otherVersions;
    const v = latestOf(list)?.number;
    return Number.isFinite(v) ? v + 1 : null;
  };
  const saveAs = (id) => {
    const v = id ? nextVersion(id) : null;
    return v ? ` and save v${v}` : "";
  };
  const rowsText = `${n} ${n === 1 ? "row" : "rows"}`;
  // P6: one verb on this dataset — Tag (toolbar `Tag rows…`, the title,
  // the button), never `Add 3 rows` while nothing is picked.
  const actionLabel =
    target === "same"
      ? splitNames.length
        ? `Tag ${rowsText} as ${splitNames.join(", ")}${saveAs(datasetId)}`
        : updateExpected
          ? // IT-R4 G3 (P5): the button says what it replaces; integration
            // review 2: only the rows whose expected really changes.
            expectedChanges
            ? `${expectedPlan ? replaceExpectedText(expectedPlan) : `Replace ${n} expected ${n === 1 ? "value" : "values"}`}${saveAs(datasetId)}`
            : "Nothing to replace"
          : `Tag ${rowsText}`
      : target === "other"
        ? targetName
          ? `Add ${rowsText} to ${targetName}${saveAs(otherId)}`
          : `Add ${rowsText}`
        : `Add ${rowsText} to ${newName.trim() || "a new dataset"}`;
  const draft = splitDraft.trim();
  const draftExists = splits.some((s) => s.name === draft);
  // P7: a disabled save always says why, inline.
  const blockedReason = ready
    ? null
    : !exampleIds.length
      ? "Select rows first"
      : noOp
        ? noOp
        : target === "same"
          ? "Pick a split to tag the rows"
          : target === "other"
            ? !otherId
              ? "Choose a dataset"
              : colsReady
                ? noColumnsMapReason(datasetName, targetName)
                : "Reading the columns…"
            : "Name the new dataset";
  const suggested = SUGGESTED_SPLITS.filter(
    (name) => !splits.some((s) => s.name === name) && !newSplits.includes(name),
  );
  return (
    <CustomDialog
      open={open}
      onClose={() => onClose(false)}
      title={dialogTitleFor(target, n, {
        splits: splitNames.length > 0,
        updateExpected,
        runLabel: run?.label,
      })}
      actionButton={actionLabel}
      onClickAction={() => submit()}
      isData={ready}
      loading={add.isPending}
    >
      <DialogContent sx={{ p: 0, m: 0 }}>
        {/* P6: one height for every state (picking a split never moves the
            dialog). */}
        <Stack
          spacing={2}
          sx={{ pt: 0.5, minHeight: "min(520px, calc(100vh - 260px))" }}
        >
          <Box>
            <Label>Target</Label>
            <RadioGroup
              value={target}
              onChange={(e) => setTarget(e.target.value)}
            >
              <FormControlLabel
                value="same"
                control={<Radio size="small" />}
                label={
                  <Typography typography="s1">{`This dataset (${datasetName ?? "current"})`}</Typography>
                }
              />
              <FormControlLabel
                value="other"
                control={<Radio size="small" />}
                label={<Typography typography="s1">Another dataset</Typography>}
              />
              <FormControlLabel
                value="new"
                control={<Radio size="small" />}
                label={<Typography typography="s1">New dataset</Typography>}
              />
            </RadioGroup>
            {target === "other" && (
              <Select
                fullWidth
                size="small"
                displayEmpty
                value={otherId}
                onChange={(e) => setOtherId(e.target.value)}
                sx={{ mt: 1 }}
                inputProps={{ "aria-label": "Target dataset" }}
              >
                <MenuItem value="" disabled>
                  Choose a dataset
                </MenuItem>
                {others.map((d) => (
                  <MenuItem key={d.datasetId} value={d.datasetId}>
                    {d.name}
                  </MenuItem>
                ))}
              </Select>
            )}
            {target === "other" && colsReady ? (
              <ColumnsStep
                sourceCols={sourceCols}
                targetCols={targetCols}
                map={columnMap}
                onChange={(id, v) =>
                  setColumnMap((m) => ({ ...m, [id]: v || null }))
                }
                count={mapCount}
              />
            ) : null}
            {target === "new" && (
              <TextField
                fullWidth
                size="small"
                placeholder="Dataset name"
                value={newName}
                onChange={(e) => {
                  setNewName(e.target.value);
                  setNameError("");
                }}
                error={Boolean(nameError)}
                helperText={nameError || undefined}
                FormHelperTextProps={{
                  "data-testid": "add-to-dataset-name-error",
                }}
                sx={{ mt: 1 }}
                inputProps={{ "aria-label": "New dataset name" }}
              />
            )}
          </Box>

          {runs.length > 1 && (
            <Box>
              <Label>Outputs from</Label>
              <Select
                fullWidth
                size="small"
                value={runId ?? ""}
                onChange={(e) => setRunId(e.target.value)}
              >
                {runs.map((r) => (
                  <MenuItem key={r.run_id} value={r.run_id}>
                    {`${r.label} ${r.name ?? ""}`.trim()}
                  </MenuItem>
                ))}
              </Select>
            </Box>
          )}
          {tries > 1 && readsOutput ? (
            // integration (DI-06 d): which try's output is read (default 1);
            // shown only when the action reads the output.
            <Stack
              direction="row"
              alignItems="center"
              gap={1}
              data-testid="add-to-dataset-trial"
            >
              <Typography typography="s2">Output of try</Typography>
              <Select
                size="small"
                value={Math.min(trial, tries)}
                onChange={(e) => setTrial(Number(e.target.value))}
                inputProps={{ "aria-label": "Try" }}
                sx={{ minWidth: 64 }}
              >
                {Array.from({ length: tries }, (_, i) => (
                  <MenuItem key={i + 1} value={i + 1}>
                    {i + 1}
                  </MenuItem>
                ))}
              </Select>
              <Typography typography="s2">{`of ${tries}`}</Typography>
            </Stack>
          ) : null}

          {target === "same" ? (
            <>
              <Box>
                <Label>Split</Label>
                <Stack
                  direction="row"
                  gap={0.75}
                  flexWrap="wrap"
                  sx={{ mb: 1 }}
                >
                  {splits.map((s) => {
                    const on = splitIds.includes(s.id);
                    return (
                      <Chip
                        key={s.id}
                        size="small"
                        // IT-R5 G3 (S4): the name, then a `3/3 in` badge
                        // (the count is not part of the name); the full
                        // sentence is the hover title.
                        label={
                          <Box
                            component="span"
                            sx={{
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 0.5,
                            }}
                          >
                            <span>{s.name}</span>
                            {membership[s.id] > 0 ? (
                              <Box
                                component="span"
                                data-testid={`split-chip-in-${s.name}`}
                                sx={{
                                  px: 0.5,
                                  borderRadius: 0.5,
                                  typography: "s3",
                                  lineHeight: "16px",
                                  fontVariantNumeric: "tabular-nums",
                                  bgcolor: on
                                    ? "rgba(255,255,255,0.24)"
                                    : "action.selected",
                                }}
                              >
                                {`${membership[s.id]}/${n} in`}
                              </Box>
                            ) : null}
                          </Box>
                        }
                        title={splitChipLabel(s.name, membership[s.id], n)}
                        aria-label={s.name}
                        aria-pressed={on}
                        data-testid={`split-chip-${s.name}`}
                        variant={on ? "filled" : "outlined"}
                        color={on ? "primary" : "default"}
                        icon={
                          on ? (
                            <Iconify icon="mdi:check" width={14} />
                          ) : undefined
                        }
                        onClick={() =>
                          setSplitIds((ids) =>
                            on ? ids.filter((x) => x !== s.id) : [...ids, s.id],
                          )
                        }
                        sx={{ borderRadius: 0.5 }}
                      />
                    );
                  })}
                  {newSplits.map((name) => (
                    <SplitChip
                      key={name}
                      split={{ id: name, name: `${name} (new)`, color: "info" }}
                      onClick={() =>
                        setNewSplits((s) => s.filter((x) => x !== name))
                      }
                    />
                  ))}
                  {/* Suggestions that do not exist yet: one click creates. */}
                  {suggested.map((name) => (
                    <Chip
                      key={`suggest-${name}`}
                      size="small"
                      label={name}
                      variant="outlined"
                      icon={<Iconify icon="mdi:plus" width={14} />}
                      onClick={() => setNewSplits((s) => [...s, name])}
                      data-testid={`suggest-split-${name}`}
                      sx={{ borderRadius: 0.5, borderStyle: "dashed" }}
                    />
                  ))}
                </Stack>
                <Stack direction="row" gap={1}>
                  <TextField
                    size="small"
                    placeholder="New split name"
                    value={splitDraft}
                    onChange={(e) => setSplitDraft(e.target.value)}
                    inputRef={splitInputRef}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        if (splitDraft.trim()) addDraftSplit();
                        else if (ready && !add.isPending) submit();
                      }
                    }}
                    inputProps={{ "aria-label": "New split name" }}
                    sx={{ flex: 1 }}
                  />
                  <Button
                    size="small"
                    variant="outlined"
                    // Empty: puts the cursor in the name field (never a
                    // dead button).
                    onClick={() =>
                      draft ? addDraftSplit() : splitInputRef.current?.focus()
                    }
                    sx={{ flexShrink: 0, maxWidth: 200 }}
                  >
                    <Box
                      component="span"
                      sx={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {draft
                        ? `${draftExists ? "Use" : "Create"} "${draft}"`
                        : "+ New split"}
                    </Box>
                  </Button>
                </Stack>
              </Box>
              <Box>
                <FormControlLabel
                  control={
                    <Checkbox
                      size="small"
                      checked={updateExpected}
                      onChange={(e) => setUpdateExpected(e.target.checked)}
                    />
                  }
                  label={
                    <Typography typography="s1">
                      {`Use ${run?.label ? `${run.label}'s` : "the run's"} output as the expected value`}
                    </Typography>
                  }
                />
                <Typography
                  typography="s3"
                  color="text.secondary"
                  sx={{ display: "block", ml: 3.75, mt: -0.5 }}
                >
                  For rows where this run is right and the expected value is out
                  of date. Saved in the new version.
                </Typography>
                {updateExpected && (
                  <Alert severity="warning" sx={{ typography: "s2", py: 0 }}>
                    Only if you have checked these outputs. The expected values
                    of these rows are replaced in the new version.
                  </Alert>
                )}
                {updateExpected && (
                  <ExpectedPreview
                    plan={expectedPlan}
                    run={run}
                    trial={pickedTrial}
                    tries={tries}
                  />
                )}
              </Box>
            </>
          ) : (
            <Box>
              <Label>Expected value</Label>
              <RadioGroup
                value={expectedFrom}
                onChange={(e) => setExpectedFrom(e.target.value)}
              >
                <FormControlLabel
                  value="keep"
                  control={<Radio size="small" />}
                  label={
                    <Typography typography="s1">
                      {"Keep the row's expected"}
                    </Typography>
                  }
                />
                <FormControlLabel
                  value="none"
                  control={<Radio size="small" />}
                  label={
                    <Typography typography="s1">
                      Leave empty to fill in later
                    </Typography>
                  }
                />
                <FormControlLabel
                  value="output"
                  control={<Radio size="small" />}
                  label={
                    <Typography typography="s1">{`Use ${run?.label ? `${run.label}'s` : "the run's"} output as the expected value`}</Typography>
                  }
                />
                <FormControlLabel
                  value="output_review"
                  control={<Radio size="small" />}
                  label={
                    <Typography typography="s1">{`Use ${run?.label ? `${run.label}'s` : "the run's"} reply as expected · needs review`}</Typography>
                  }
                />
              </RadioGroup>
              {expectedFrom === "output" && (
                <Alert severity="warning" sx={{ typography: "s2", py: 0 }}>
                  Only if you have checked these outputs.
                </Alert>
              )}
              {expectedFrom === "output_review" && <ProductionWarningChip />}
            </Box>
          )}

          {serverError ? (
            <Alert
              severity="error"
              data-testid="add-to-dataset-server-error"
              onClose={() => setServerError("")}
              sx={{ typography: "s2", py: 0 }}
            >
              {serverError}
            </Alert>
          ) : null}
          {/* P6: the reason line keeps its place (the dialog never jumps). */}
          <Typography
            typography="s2"
            color={noOp ? "warning.dark" : "text.secondary"}
            data-testid="add-to-dataset-reason"
            sx={{ minHeight: 20, mt: "8px !important" }}
          >
            {blockedReason ?? ""}
          </Typography>
          <Box>
            <Label>Version note</Label>
            <TextField
              fullWidth
              size="small"
              multiline
              minRows={2}
              value={description}
              onChange={(e) => {
                setTouchedDescription(true);
                setDescription(e.target.value);
              }}
              placeholder="What changed in this version"
              inputProps={{ "aria-label": "Version description" }}
            />
            <Typography
              typography="s3"
              color="text.disabled"
              sx={{ mt: 0.5, display: "block" }}
            >
              {target === "same"
                ? "Saved as a new version of this dataset"
                : "Rows are copied with a link back to this row and run"}
            </Typography>
            {target === "same" ? (
              // P6: a version without a tag (the note alone).
              <Button
                size="small"
                variant="text"
                disabled={add.isPending || !exampleIds.length}
                onClick={() => {
                  setSavingPlain(true);
                  submit({ plain: true });
                }}
                startIcon={
                  <Iconify icon="mdi:content-save-outline" width={16} />
                }
                data-testid="add-to-dataset-save-plain"
                sx={{ mt: 0.5, px: 0.75, typography: "s2" }}
              >
                {savingPlain
                  ? "Saving…"
                  : `Save a version without tagging${saveAs(datasetId).replace(" and save", " ·")}`}
              </Button>
            ) : null}
          </Box>
        </Stack>
      </DialogContent>
    </CustomDialog>
  );
};

AddResultsToDatasetDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  datasetId: PropTypes.string.isRequired,
  datasetName: PropTypes.string,
  exampleIds: PropTypes.arrayOf(PropTypes.string).isRequired,
  rows: PropTypes.array,
  runs: PropTypes.array.isRequired,
  baselineId: PropTypes.string,
  pills: PropTypes.array,
  focusedRunId: PropTypes.string,
  splits: PropTypes.array.isRequired,
};

export default AddResultsToDatasetDialog;
