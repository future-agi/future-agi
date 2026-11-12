import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
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
import Iconify from "src/components/iconify";
import CustomDialog from "src/sections/develop-detail/Common/CustomDialog/CustomDialog";
import {
  useDevelopDatasetList,
  useGetDatasetColumns,
} from "src/api/develop/develop-detail";
import { useAddResultsToDataset } from "src/api/develop/run-results";
import { ProductionWarningChip } from "src/sections/common/AddData/ExpectedSourceControl";
import {
  MAX_COPY_COLUMNS,
  MAX_COPY_ROWS,
  MAX_COPY_TEXT,
  copyBody,
  copyDoneText,
  copyErrorOf,
  copyExpectedFrom,
  copyFailedText,
  copyOutcomeOf,
  copyPartialText,
  copySources,
  copyUnknownText,
  crossMapText,
  crossMappedColumns,
  defaultCopyMap,
  defaultDescription,
  defaultIncluded,
  dialogTitleFor,
  failureGroups,
  isExternalRun,
  isRetryableFailure,
  nameMayExistText,
  newColumnNames,
  retryBody,
  savedInputColumns,
  sharedTargetName,
  sourceKind,
  sourceRunFor,
} from "./addToDatasetModel";

const Label = ({ children, id }) => (
  <Typography id={id} typography="s2" color="text.secondary" sx={{ mb: 0.5 }}>
    {children}
  </Typography>
);
Label.propTypes = { children: PropTypes.node, id: PropTypes.string };

const rowsWord = (n) => `${n} ${n === 1 ? "row" : "rows"}`;

/** `Row 18, Row 20 and 3 more` (rows not on the page are counted). */
const labelsText = (labels, count) => {
  const shown = labels.slice(0, 5);
  const more = count - shown.length;
  if (!shown.length) return "";
  return `${shown.join(", ")}${more > 0 ? ` and ${more} more` : ""}`;
};

/** The rows that could not be copied, one line per reason. */
function FailureList({ groups }) {
  if (!groups.length) return null;
  return (
    <Box
      component="ul"
      data-testid="add-to-dataset-failures"
      sx={{ m: 0, mt: 0.75, pl: 2.5 }}
    >
      {groups.map((g) => (
        <Typography key={g.text} component="li" typography="s2">
          {`${rowsWord(g.count)}: ${g.text}`}
          {g.labels.length ? (
            <Typography
              component="span"
              typography="s3"
              color="text.secondary"
              sx={{ display: "block" }}
            >
              {labelsText(g.labels, g.count)}
            </Typography>
          ) : null}
        </Typography>
      ))}
    </Box>
  );
}

FailureList.propTypes = { groups: PropTypes.array.isRequired };

/**
 * The Columns step: one line per result column (inputs, expected, output,
 * scores). A new dataset ticks the lines it copies and shows the column each
 * becomes; another dataset picks a column of its own for each, or Skip.
 */
function CopyColumns({
  target,
  lines,
  targetCols,
  map,
  onMap,
  included,
  onInclude,
  forced,
  names,
  countText,
}) {
  return (
    <Box
      data-testid="add-to-dataset-columns"
      sx={{
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
          {countText}
        </Typography>
      </Stack>
      <Stack spacing={0.75}>
        {lines.map((l) => {
          const locked = forced.includes(l.key);
          return (
            <Stack
              key={l.key}
              direction="row"
              alignItems="center"
              gap={1}
              data-testid={`add-to-dataset-column-${l.key}`}
            >
              {target === "new" ? (
                <Checkbox
                  size="small"
                  checked={locked || Boolean(included[l.key])}
                  disabled={locked}
                  onChange={(e) => onInclude(l.key, e.target.checked)}
                  inputProps={{ "aria-label": `Copy ${l.label}` }}
                  sx={{ p: 0.25 }}
                />
              ) : null}
              <Typography
                typography="s2"
                noWrap
                title={l.label}
                sx={{ flex: 1, minWidth: 0 }}
              >
                {l.label}
              </Typography>
              <Iconify icon="mdi:arrow-right" width={14} />
              {target === "new" ? (
                <Typography
                  typography="s2"
                  noWrap
                  title={names[l.key] ?? ""}
                  color={names[l.key] ? "text.primary" : "text.disabled"}
                  sx={{ flex: 1, minWidth: 0 }}
                >
                  {names[l.key] ?? "Not copied"}
                </Typography>
              ) : (
                <Select
                  size="small"
                  displayEmpty
                  value={map[l.key] ?? ""}
                  onChange={(e) => onMap(l.key, e.target.value)}
                  inputProps={{ "aria-label": `Target column for ${l.label}` }}
                  sx={{ flex: 1, minWidth: 0, typography: "s2" }}
                >
                  <MenuItem value="">Skip</MenuItem>
                  {targetCols.map((t) => (
                    <MenuItem key={t.id} value={t.id}>
                      {t.name}
                    </MenuItem>
                  ))}
                </Select>
              )}
            </Stack>
          );
        })}
      </Stack>
    </Box>
  );
}

CopyColumns.propTypes = {
  target: PropTypes.oneOf(["new", "other"]).isRequired,
  lines: PropTypes.array.isRequired,
  targetCols: PropTypes.array.isRequired,
  map: PropTypes.object.isRequired,
  onMap: PropTypes.func.isRequired,
  included: PropTypes.object.isRequired,
  onInclude: PropTypes.func.isRequired,
  forced: PropTypes.array.isRequired,
  names: PropTypes.object.isRequired,
  countText: PropTypes.string.isRequired,
};

/**
 * Add result rows to a dataset (PRODUCT-SPEC §3.10.8, C11): the picked rows'
 * saved inputs, the run's output and the chosen scores are copied into a new
 * dataset or another one (through a column map), with a link back to the
 * row and run. Every picked row is either added or listed with its reason.
 */
const AddResultsToDatasetDialog = ({
  open,
  onClose,
  datasetId,
  datasetName,
  exampleIds,
  rows = [],
  runs,
  baselineId,
  pills,
  focusedRunId,
  metrics = [],
}) => {
  const navigate = useNavigate();
  const [target, setTarget] = useState("new");
  const [otherId, setOtherId] = useState("");
  const [newName, setNewName] = useState("");
  const [runId, setRunId] = useState(null);
  const [expectedChoice, setExpectedChoice] = useState("keep");
  const [note, setNote] = useState("");
  const [touchedNote, setTouchedNote] = useState(false);
  const [trial, setTrial] = useState(1);
  // Another dataset's picks, for the dataset they were made on.
  const [picks, setPicks] = useState({ otherId: "", map: {} });
  const [included, setIncluded] = useState({});
  const [nameError, setNameError] = useState("");
  // A refusal, an all-rows failure or an answer that never came back.
  const [problem, setProblem] = useState(null);
  // Some rows were added: the dialog stays open on the rows that were not.
  const [partial, setPartial] = useState(null);
  const nameInputRef = useRef(null);
  const sentRef = useRef(null);
  // Names of new datasets whose request got no answer: they may exist.
  const unconfirmedRef = useRef([]);

  const {
    data: datasets = [],
    isLoading: datasetsLoading,
    isError: datasetsError,
  } = useDevelopDatasetList(undefined, undefined, { enabled: open });
  const others = datasets.filter((d) => d.datasetId !== datasetId);
  const run = runs.find((r) => r.run_id === runId);
  const tries = Math.max(1, Number(run?.trials) || 1);
  const external = isExternalRun(run);
  const {
    data: sourceCols = [],
    isFetched: sourceColsFetched,
    isError: sourceColsError,
  } = useGetDatasetColumns(datasetId, { enabled: open && !external });
  const {
    data: targetCols = [],
    isFetched: targetColsFetched,
    isError: targetColsError,
  } = useGetDatasetColumns(otherId || null, {
    enabled: open && target === "other" && Boolean(otherId),
  });

  // Reset on open: a new dataset, the page's run, the row's expected.
  useEffect(() => {
    if (!open) return;
    setTarget("new");
    setOtherId("");
    setNewName("");
    setRunId(sourceRunFor({ pills, runs, baselineId, focusedRunId }));
    setExpectedChoice("keep");
    setNote("");
    setTouchedNote(false);
    setTrial(1);
    setPicks({ otherId: "", map: {} });
    setIncluded({});
    setNameError("");
    setProblem(null);
    setPartial(null);
    sentRef.current = null;
    unconfirmedRef.current = [];
    const t = setTimeout(() => nameInputRef.current?.focus(), 120);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // A run on this dataset's rows copies the inputs it saved, keyed by this
  // dataset's column ids; a column added after the run is not offered.
  const { saved: inputCols, unsaved: unsavedCols } = external
    ? { saved: [], unsaved: [] }
    : savedInputColumns(sourceCols, rows);
  // The row's expected is a line of its own only when the inputs are copied
  // whole (an external run). Otherwise it is one of this dataset's columns,
  // left empty by unticking or skipping that column, so `Leave empty` is
  // not offered (it would change nothing).
  const offersEmpty = external || (sourceColsFetched && !inputCols.length);
  const expectedPick =
    expectedChoice === "none" && !offersEmpty ? "keep" : expectedChoice;
  const lines = copySources({
    run,
    sourceCols: inputCols,
    metrics,
    expectedChoice: expectedPick,
  });
  // The run's output used as the expected value is always copied.
  const forced =
    copyExpectedFrom(expectedPick) === "output" ? ["expected"] : [];
  const otherMap = defaultCopyMap(
    lines,
    targetCols,
    picks.otherId === otherId ? picks.map : {},
  );
  const copied = defaultIncluded(lines, included);
  const names = newColumnNames(lines, copied, forced);
  const liveMap = Object.fromEntries(
    lines.map((l) => [
      l.key,
      (target === "new" ? names[l.key] : otherMap[l.key]) ?? null,
    ]),
  );
  const mapped = lines.filter((l) => liveMap[l.key]).length;

  const n = exampleIds.length;
  const pickedTrial = Math.min(trial, tries);
  const targetName =
    target === "new"
      ? newName.trim()
      : others.find((d) => d.datasetId === otherId)?.name;
  const autoNote = defaultDescription({
    count: n,
    runLabel: run?.label,
    kind: sourceKind(pills, runId),
    splitNames: [],
    target: "other",
  });
  const noteValue = touchedNote ? note : autoNote;
  const sourceReady = external || sourceColsFetched;
  const colsReady =
    sourceReady &&
    (target === "new" || (Boolean(otherId) && targetColsFetched));
  const colsError =
    (!external && sourceColsError) ||
    (target === "other" && Boolean(otherId) && targetColsError);
  const shared =
    target === "other" ? sharedTargetName(liveMap, targetCols) : null;
  const cross =
    target === "other" && colsReady
      ? crossMappedColumns(
          lines.map((l) => ({ id: l.key, name: l.name })),
          targetCols,
          liveMap,
        )
      : [];

  // P7: a disabled button always says why, inline.
  const reasonBlocked = () => {
    if (!n) return "Select rows first";
    if (n > MAX_COPY_ROWS)
      return `Select at most ${MAX_COPY_ROWS.toLocaleString("en-US")} rows to copy at a time`;
    if (!runId) return "Pick the run to copy from";
    if (target === "new" && !newName.trim()) return "Name the new dataset";
    if (target === "new" && newName.trim().length > MAX_COPY_TEXT)
      return "Use a shorter dataset name";
    if (target === "other" && !otherId) {
      if (datasetsError)
        return "The datasets could not be loaded. Close the dialog and try again.";
      return others.length || datasetsLoading
        ? "Choose a dataset"
        : "There are no other datasets yet. Add the rows to a new dataset.";
    }
    if (colsError)
      return "The columns could not be read. Close the dialog and try again.";
    if (!colsReady) return "Reading the columns…";
    if (target === "other" && !targetCols.length)
      return `${targetName ?? "This dataset"} has no columns to copy into. Add a column to it first, or add the rows to a new dataset.`;
    if (!mapped)
      return target === "other"
        ? `None of the columns map to ${targetName ?? "this dataset"}. Pick a column for at least one.`
        : "Pick at least one column to copy";
    if (mapped > MAX_COPY_COLUMNS)
      return `Copy at most ${MAX_COPY_COLUMNS} columns at a time`;
    if (shared)
      return `Two columns go to ${shared}. Pick a different column for one of them.`;
    if (forced.length && !liveMap.expected)
      return "Pick a column for the expected value, or keep the row's expected";
    if (noteValue.length > MAX_COPY_TEXT) return "Use a shorter note";
    return null;
  };
  const blockedReason = reasonBlocked();
  const ready = !blockedReason;

  const finish = (result) => {
    enqueueSnackbar(copyDoneText(result, targetName), {
      key: "add-results-to-dataset",
      variant: "success",
      autoHideDuration: 6000,
      ...(result.link
        ? {
            action: (key) => (
              <Button
                size="small"
                color="inherit"
                onClick={() => {
                  closeSnackbar(key);
                  navigate(result.link);
                }}
                sx={{ fontWeight: 600 }}
              >
                Open
              </Button>
            ),
          }
        : {}),
    });
    onClose(true);
  };

  // The answer never came back: the rows (and a new dataset) may be there.
  // The warning names the dataset that request went to.
  const markUnknown = (sent) => {
    const toNew = !sent?.retry && sent?.target === "new";
    if (toNew && sent.body?.new_dataset_name)
      unconfirmedRef.current = [
        ...unconfirmedRef.current,
        sent.body.new_dataset_name,
      ];
    setProblem({
      kind: "unknown",
      link: sent?.link ?? null,
      name:
        (sent?.retry
          ? partial?.datasetName
          : toNew
            ? sent.body?.new_dataset_name
            : targetName) ?? null,
    });
  };

  // After a retry: the rows not sent again stay listed with their reason,
  // next to the rows of this answer that still were not added.
  const afterRetry = (failures, addedNow) => {
    const resent = new Set(sentRef.current?.resent ?? []);
    const left = [
      ...partial.failures.filter((f) => !resent.has(f.example_id)),
      ...failures,
    ];
    return {
      ...partial,
      added: partial.added + addedNow,
      failed: left.length,
      failures: left,
    };
  };

  // No automatic retries: a repeated request can add the rows twice, so
  // every outcome is said here and the person decides.
  const add = useAddResultsToDataset(datasetId, {
    onSuccess: (res) => {
      const sent = sentRef.current;
      const out = copyOutcomeOf(res);
      if (out.kind === "unknown") {
        markUnknown(sent);
        return;
      }
      if (sent?.retry && partial) {
        const next = afterRetry(out.result.failures, out.result.added);
        if (!next.failures.length) {
          finish(next);
          return;
        }
        // Some rows are still not added: they stay listed, never dropped.
        setProblem(out.result.added ? null : { kind: "retryFailed" });
        setPartial(next);
        return;
      }
      if (out.kind === "completed") {
        finish(out.result);
        return;
      }
      if (out.kind === "failed") {
        setProblem({
          kind: "failed",
          result: out.result,
          newDataset: sent?.target === "new",
        });
        return;
      }
      // Some rows added: the rest stay listed, never dropped.
      setProblem(null);
      setPartial(out.result);
    },
    onError: (error) => {
      const sent = sentRef.current;
      const err = copyErrorOf(error);
      if (err.kind === "failed") {
        if (sent?.retry && partial) {
          setPartial(afterRetry(err.result.failures, 0));
          setProblem({ kind: "retryFailed" });
        } else
          setProblem({
            kind: "failed",
            result: err.result,
            newDataset: sent?.target === "new",
          });
        return;
      }
      if (err.kind === "unknown") {
        markUnknown(sent);
        return;
      }
      // A name the server refuses is said under the name field.
      if (sent?.target === "new" && !sent.retry && /name/i.test(err.message)) {
        // A request under this name got no answer earlier: the dataset
        // that request made may be the one that has the name now.
        const mayBeThatDataset =
          unconfirmedRef.current.includes(sent.body.new_dataset_name) &&
          /already exists/i.test(err.message);
        setNameError(
          mayBeThatDataset
            ? nameMayExistText(sent.body.new_dataset_name)
            : err.message,
        );
        // Nothing was added this time; the earlier outcome is still unknown.
        if (sent.priorUnknown) setProblem(sent.priorUnknown);
        return;
      }
      setProblem({ kind: "refused", message: err.message });
    },
  });

  const submit = () => {
    if (!ready || add.isPending) return;
    const body = copyBody({
      datasetId,
      runId,
      exampleIds,
      target,
      trial: pickedTrial,
      expectedChoice: expectedPick,
      columnMap: liveMap,
      newDatasetName: newName.trim(),
      note: noteValue,
    });
    const targetId = target === "new" ? "new" : otherId;
    sentRef.current = {
      target,
      body,
      retry: false,
      priorUnknown: problem?.kind === "unknown" ? problem : null,
      link:
        target === "other"
          ? `/dashboard/develop/${encodeURIComponent(otherId)}?tab=data`
          : null,
    };
    setProblem(null);
    setNameError("");
    add.mutate({ targetId, ...body });
  };

  const retryFailed = () => {
    if (!partial?.datasetId || !sentRef.current || add.isPending) return;
    const body = retryBody(sentRef.current.body, partial);
    if (!body.example_ids.length) return;
    sentRef.current = {
      ...sentRef.current,
      target: "other",
      retry: true,
      priorUnknown: null,
      resent: body.example_ids,
      link: partial.link,
    };
    setProblem(null);
    add.mutate({ targetId: partial.datasetId, ...body });
  };

  const changeTarget = (next) => {
    setTarget(next);
    setProblem(null);
  };

  const problemAlert = (() => {
    if (!problem) return null;
    if (problem.kind === "unknown")
      return (
        <Alert
          severity="warning"
          data-testid="add-to-dataset-unknown"
          sx={{ typography: "s2" }}
          action={
            problem.link ? (
              <Button
                size="small"
                color="inherit"
                onClick={() => {
                  onClose(false);
                  navigate(problem.link);
                }}
              >
                Check the dataset
              </Button>
            ) : undefined
          }
        >
          {copyUnknownText(problem.name ?? partial?.datasetName ?? targetName)}
        </Alert>
      );
    if (problem.kind === "failed")
      return (
        <Alert
          severity="error"
          data-testid="add-to-dataset-failed"
          sx={{ typography: "s2" }}
        >
          {copyFailedText(problem.result, { newDataset: problem.newDataset })}
          <FailureList groups={failureGroups(problem.result.failures, rows)} />
        </Alert>
      );
    if (problem.kind === "retryFailed")
      return (
        <Alert
          severity="info"
          data-testid="add-to-dataset-retry-failed"
          sx={{ typography: "s2" }}
        >
          None of these rows were added this time.
        </Alert>
      );
    return (
      <Alert
        severity="error"
        data-testid="add-to-dataset-server-error"
        onClose={() => setProblem(null)}
        sx={{ typography: "s2" }}
      >
        {problem.message}
      </Alert>
    );
  })();

  if (partial) {
    const left = partial.failures.length;
    // Only a try that had no finished result (or a reason this page does not
    // know) can be added by sending again; the rest would fail the same way.
    const retryable = partial.failures.filter(isRetryableFailure).length;
    const retryLabel =
      retryable === left
        ? retryable === 1
          ? "Try the failed row again"
          : `Try the ${retryable} failed rows again`
        : `Try ${rowsWord(retryable)} again`;
    return (
      <CustomDialog
        open={open}
        onClose={() => onClose(false)}
        title={`Added ${partial.added} of ${rowsWord(partial.requested)}`}
        actionButton="Done"
        onClickAction={() => onClose(false)}
        isData
        loading={add.isPending}
      >
        <DialogContent sx={{ p: 0, m: 0 }}>
          <Stack spacing={1.5} sx={{ pt: 0.5 }}>
            <Alert
              severity="warning"
              data-testid="add-to-dataset-partial"
              sx={{ typography: "s2" }}
            >
              {copyPartialText(partial, targetName)}
              <FailureList groups={failureGroups(partial.failures, rows)} />
            </Alert>
            {problemAlert}
            <Stack direction="row" gap={1} flexWrap="wrap">
              {partial.link ? (
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => {
                    onClose(false);
                    navigate(partial.link);
                  }}
                  startIcon={<Iconify icon="mdi:open-in-new" width={16} />}
                >
                  {`Open ${partial.datasetName ?? "the dataset"}`}
                </Button>
              ) : null}
              {partial.datasetId && retryable ? (
                <Button
                  size="small"
                  variant="text"
                  disabled={add.isPending}
                  onClick={retryFailed}
                  startIcon={<Iconify icon="mdi:refresh" width={16} />}
                >
                  {retryLabel}
                </Button>
              ) : null}
            </Stack>
            {left > retryable ? (
              <Typography
                typography="s3"
                color="text.secondary"
                data-testid="add-to-dataset-retry-note"
              >
                {retryable
                  ? "Only rows that a second send can add are sent again. The others would fail the same way."
                  : "Sending these rows again would fail the same way."}
              </Typography>
            ) : null}
            <Typography typography="s3" color="text.secondary">
              Closing keeps your selection.
            </Typography>
          </Stack>
        </DialogContent>
      </CustomDialog>
    );
  }

  const rowsText = rowsWord(n);
  const actionLabel =
    problem?.kind === "unknown"
      ? "Send again"
      : target === "other"
        ? targetName
          ? `Add ${rowsText} to ${targetName}`
          : `Add ${rowsText}`
        : `Add ${rowsText} to ${newName.trim() || "a new dataset"}`;
  const who = run?.label ? `${run.label}'s` : "the run's";
  return (
    <CustomDialog
      open={open}
      onClose={() => onClose(false)}
      title={dialogTitleFor("other", n, {})}
      actionButton={actionLabel}
      onClickAction={submit}
      isData={ready}
      loading={add.isPending}
    >
      <DialogContent sx={{ p: 0, m: 0 }}>
        <Stack spacing={2} sx={{ pt: 0.5 }}>
          <Box>
            <Label id="add-to-dataset-target-label">Add to</Label>
            <RadioGroup
              aria-labelledby="add-to-dataset-target-label"
              value={target}
              onChange={(e) => changeTarget(e.target.value)}
            >
              <FormControlLabel
                value="new"
                control={<Radio size="small" />}
                label={<Typography typography="s1">New dataset</Typography>}
              />
              <FormControlLabel
                value="other"
                control={<Radio size="small" />}
                label={<Typography typography="s1">Another dataset</Typography>}
              />
            </RadioGroup>
            {target === "new" ? (
              <TextField
                fullWidth
                size="small"
                placeholder="Dataset name"
                value={newName}
                inputRef={nameInputRef}
                onChange={(e) => {
                  setNewName(e.target.value);
                  setNameError("");
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    submit();
                  }
                }}
                error={Boolean(nameError)}
                helperText={nameError || undefined}
                FormHelperTextProps={{
                  "data-testid": "add-to-dataset-name-error",
                }}
                sx={{ mt: 1 }}
                inputProps={{ "aria-label": "New dataset name" }}
              />
            ) : (
              <Select
                fullWidth
                size="small"
                displayEmpty
                value={otherId}
                onChange={(e) => {
                  setOtherId(e.target.value);
                  setProblem(null);
                }}
                sx={{ mt: 1 }}
                inputProps={{ "aria-label": "Target dataset" }}
              >
                <MenuItem value="" disabled>
                  {datasetsLoading ? "Loading datasets…" : "Choose a dataset"}
                </MenuItem>
                {others.map((d) => (
                  <MenuItem key={d.datasetId} value={d.datasetId}>
                    {d.name}
                  </MenuItem>
                ))}
              </Select>
            )}
          </Box>

          {runs.length > 1 && (
            <Box>
              <Label>Results from</Label>
              <Select
                fullWidth
                size="small"
                value={runId ?? ""}
                onChange={(e) => {
                  setRunId(e.target.value);
                  setProblem(null);
                }}
                inputProps={{ "aria-label": "Results from" }}
              >
                {runs.map((r) => (
                  <MenuItem key={r.run_id} value={r.run_id}>
                    {`${r.label} ${r.name ?? ""}`.trim()}
                  </MenuItem>
                ))}
              </Select>
            </Box>
          )}
          {tries > 1 ? (
            // integration (DI-06 d): the one try whose output and scores
            // are copied (default 1), never an average of tries.
            <Stack
              direction="row"
              alignItems="center"
              gap={1}
              data-testid="add-to-dataset-trial"
            >
              <Typography typography="s2">Copy try</Typography>
              <Select
                size="small"
                value={pickedTrial}
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

          <Box>
            <Label id="add-to-dataset-expected-label">Expected value</Label>
            <RadioGroup
              aria-labelledby="add-to-dataset-expected-label"
              value={expectedPick}
              onChange={(e) => setExpectedChoice(e.target.value)}
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
              {offersEmpty ? (
                <FormControlLabel
                  value="none"
                  control={<Radio size="small" />}
                  label={
                    <Typography typography="s1">
                      Leave empty to fill in later
                    </Typography>
                  }
                />
              ) : null}
              <FormControlLabel
                value="output"
                control={<Radio size="small" />}
                label={
                  <Typography typography="s1">{`Use ${who} output as the expected value`}</Typography>
                }
              />
              <FormControlLabel
                value="output_review"
                control={<Radio size="small" />}
                label={
                  <Typography typography="s1">{`Use ${who} reply as expected · needs review`}</Typography>
                }
              />
            </RadioGroup>
            {!offersEmpty && sourceColsFetched && expectedPick === "keep" ? (
              <Typography
                typography="s3"
                color="text.secondary"
                data-testid="add-to-dataset-expected-hint"
                sx={{ display: "block" }}
              >
                {target === "new"
                  ? "The row's expected is copied with this dataset's columns. To leave it empty, untick its column under Columns."
                  : "The row's expected is copied with this dataset's columns. To leave it empty, set its column to Skip under Columns."}
              </Typography>
            ) : null}
            {expectedPick === "output" && (
              <Alert severity="warning" sx={{ typography: "s2", py: 0 }}>
                Only if you have checked these outputs.
              </Alert>
            )}
            {expectedPick === "output_review" && <ProductionWarningChip />}
          </Box>

          {colsReady && !colsError ? (
            <CopyColumns
              target={target}
              lines={lines}
              targetCols={targetCols}
              map={otherMap}
              onMap={(key, value) =>
                setPicks({
                  otherId,
                  map: { ...otherMap, [key]: value || null },
                })
              }
              included={copied}
              onInclude={(key, on) => setIncluded({ ...copied, [key]: on })}
              forced={forced}
              names={names}
              countText={
                target === "new"
                  ? `${mapped} new ${mapped === 1 ? "column" : "columns"}`
                  : `${mapped} of ${lines.length} ${lines.length === 1 ? "column" : "columns"} map`
              }
            />
          ) : null}
          {colsReady && !colsError && unsavedCols.length > 0 ? (
            <Typography
              typography="s3"
              color="text.secondary"
              data-testid="add-to-dataset-columns-unsaved"
            >
              {`Not saved with this run, so not copied: ${unsavedCols
                .map((c) => c.name)
                .join(", ")}.`}
            </Typography>
          ) : null}
          {cross.length > 0 && (
            <Alert
              severity="warning"
              sx={{ typography: "s2" }}
              data-testid="add-to-dataset-columns-cross"
            >
              {crossMapText(cross)}
            </Alert>
          )}

          {problemAlert}
          {/* P6: the reason line keeps its place (the dialog never jumps). */}
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="add-to-dataset-reason"
            sx={{ minHeight: 20, mt: "8px !important" }}
          >
            {blockedReason ?? ""}
          </Typography>
          <Box>
            <Label>Note</Label>
            <TextField
              fullWidth
              size="small"
              multiline
              minRows={2}
              value={noteValue}
              onChange={(e) => {
                setTouchedNote(true);
                setNote(e.target.value);
              }}
              placeholder="Why these rows were added"
              inputProps={{ "aria-label": "Note" }}
            />
            <Typography
              typography="s3"
              color="text.disabled"
              sx={{ mt: 0.5, display: "block" }}
            >
              {`Rows are copied from ${datasetName ?? "this dataset"} with this note and a link back to each row and run. Rows already in the destination are not changed.`}
            </Typography>
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
  metrics: PropTypes.array,
};

export default AddResultsToDatasetDialog;
