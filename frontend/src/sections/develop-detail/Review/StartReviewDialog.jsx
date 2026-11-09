import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  MenuItem,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useAuthContext } from "src/auth/hooks";
import { useDatasetRuns } from "src/api/develop/runs";
import {
  fetchNextReviewItemId,
  fetchResultExampleIds,
  useExperimentReviewQueues,
  useResultExampleIds,
  useResultSummary,
} from "src/api/develop/review";
import { useQueryClient } from "@tanstack/react-query";
import {
  annotationLabelEndpoints,
  useAnnotationLabelsList,
} from "src/api/annotation-labels/annotation-labels";
import axios from "src/utils/axios";
import notify from "src/sections/common/ux/toast";
import {
  addQueueItemsQuiet,
  createQueueQuiet,
  refreshQueues,
  setQueueStatusQuiet,
} from "./reviewQueueApi";
import LabelPicker from "src/sections/annotations/queues/components/label-picker";
import AnnotatorPicker from "src/sections/annotations/queues/components/annotator-picker";
import { QUEUE_ROLES } from "src/sections/annotations/queues/constants";
import { runTitle } from "src/sections/common/Runs/runLabel";
import {
  ORDERS,
  defaultQueueName,
  matchingReviewQueues,
  orderSort,
  PAIRWISE_LABEL,
  primaryMetricKey,
  queueMetadata,
  reviewItems,
  reviewPageUrl,
  seededShuffle,
} from "./reviewModel";
// W4 C1 / C3: corrections of the expected answer; outside reviewers.
import CorrectionsOption from "./CorrectionsOption";
import { OutsideReviewersStart } from "./OutsideReviewersSection";
import { sendOutsideInvites } from "./outsideInvites";
import { outsideProblem } from "./inviteModel";

const REVIEWABLE = new Set([
  "Completed",
  "PartialCompleted",
  "Running",
  "Cancelled",
  "ExperimentEvaluation",
]);
const RUBRIC_DEFAULT = ["Correct?", "Quality"];

function Field({ label, hint, children }) {
  return (
    <Stack spacing={0.75}>
      <Stack direction="row" alignItems="baseline" gap={1}>
        <Typography typography="s1" fontWeight="fontWeightMedium">
          {label}
        </Typography>
        {hint ? (
          <Typography typography="s3" color="text.secondary">
            {hint}
          </Typography>
        ) : null}
      </Stack>
      {children}
    </Stack>
  );
}

Field.propTypes = {
  label: PropTypes.string,
  hint: PropTypes.string,
  children: PropTypes.node,
};

const count = (n) => (Number.isFinite(n) ? ` (${n})` : "");

/**
 * Start a review (§3.12): only what the queue drawer does not have — the
 * run pair (A = baseline run, B = candidate) or the run for a rubric, the
 * rows, their order and blind — over the existing queue create + add-items
 * calls. `Start reviewing` lands on the review page.
 */
const StartReviewDialog = ({
  open,
  onClose,
  datasetId,
  mode: initialMode = "pairwise",
  runA: initialA,
  runB: initialB,
  exampleIds = [],
  returnTo,
  filters = [],
}) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { user } = useAuthContext();
  const [mode, setMode] = useState(initialMode);
  const [runA, setRunA] = useState(initialA ?? "");
  const [runB, setRunB] = useState(initialB ?? "");
  const [rubricRun, setRubricRun] = useState(initialB ?? initialA ?? "");
  const [rows, setRows] = useState(null);
  const [order, setOrder] = useState("regressions");
  const [blind, setBlind] = useState(true);
  const [queueMode, setQueueMode] = useState(null);
  const [existingId, setExistingId] = useState("");
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [labelIds, setLabelIds] = useState(null);
  const [annotators, setAnnotators] = useState([]);
  const [perItem, setPerItem] = useState(1);
  const [instructions, setInstructions] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [corrections, setCorrections] = useState(null);
  const [outside, setOutside] = useState(null);

  // Reset to the caller's prefill every time the dialog opens.
  useEffect(() => {
    if (!open) return;
    setMode(initialMode);
    setRunA(initialA ?? "");
    setRunB(initialB ?? "");
    setRubricRun(initialB ?? initialA ?? "");
    setRows(null);
    setOrder("regressions");
    setBlind(initialMode === "pairwise");
    setQueueMode(null);
    setExistingId("");
    setNameTouched(false);
    setLabelIds(null);
    setPerItem(1);
    setInstructions("");
    setError(null);
    setCorrections(null);
    setOutside(null);
    setAnnotators(
      user?.id
        ? [
            {
              userId: String(user.id),
              role: QUEUE_ROLES.MANAGER,
              roles: [
                QUEUE_ROLES.MANAGER,
                QUEUE_ROLES.REVIEWER,
                QUEUE_ROLES.ANNOTATOR,
              ],
            },
          ]
        : [],
    );
  }, [open, initialMode, initialA, initialB, user?.id]);

  const runsQ = useDatasetRuns(datasetId, { enabled: open });
  const runs = useMemo(
    () => (runsQ.data?.results ?? []).filter((r) => REVIEWABLE.has(r.status)),
    [runsQ.data],
  );
  const byId = useMemo(() => new Map(runs.map((r) => [r.run_id, r])), [runs]);
  const isPair = mode === "pairwise";
  const viewRuns = isPair
    ? [runA, runB].filter(Boolean)
    : [rubricRun].filter(Boolean);
  const baseline = isPair ? runA : rubricRun;

  const queuesQ = useExperimentReviewQueues(datasetId, { enabled: open });
  const matching = useMemo(
    () =>
      matchingReviewQueues(queuesQ.data ?? [], {
        mode,
        runA,
        runB,
        runId: rubricRun,
      }),
    [queuesQ.data, mode, runA, runB, rubricRun],
  );
  const target =
    matching.find((q) => q.id === existingId) ?? matching[0] ?? null;
  const pending = target
    ? Math.max(0, (target.item_count ?? 0) - (target.completed_count ?? 0))
    : null;

  const summaryQ = useResultSummary(
    datasetId,
    { runs: viewRuns, baseline },
    { enabled: open && viewRuns.length > 0 },
  );
  const metric = primaryMetricKey(summaryQ.data?.metrics, filters);
  const sort = orderSort(order, {
    metric,
    runB: isPair ? runB : rubricRun,
    mode,
  });
  const allQ = useResultExampleIds(
    datasetId,
    { runs: viewRuns, baseline, filters: [], sort: "order" },
    { enabled: open },
  );
  const filterQ = useResultExampleIds(
    datasetId,
    { runs: viewRuns, baseline, filters, sort: "order" },
    { enabled: open && filters.length > 0 },
  );

  // Defaults that depend on loaded data: rows source and queue target.
  const rowsValue =
    rows ??
    (exampleIds.length
      ? "selected"
      : target && pending > 0
        ? "pending"
        : filters.length
          ? "filter"
          : "all");
  const queueValue = queueMode ?? (target ? "existing" : "new");
  const effectiveRows =
    rowsValue === "pending" && queueValue !== "existing" ? "all" : rowsValue;

  // Labels: pairwise prefills Overall preference; rubric Correct? + Quality.
  const labelsQ = useAnnotationLabelsList(
    { search: "", limit: 100 },
    { enabled: open },
  );
  const allLabels = useMemo(() => labelsQ.data?.results ?? [], [labelsQ.data]);
  const pairLabel = allLabels.find(
    (l) => l.settings?.kind === "pairwise" && !l.archived,
  );
  const defaultLabelIds = useMemo(
    () =>
      isPair
        ? pairLabel
          ? [pairLabel.id]
          : []
        : RUBRIC_DEFAULT.map(
            (n) => allLabels.find((l) => l.name === n)?.id,
          ).filter(Boolean),
    [isPair, pairLabel, allLabels],
  );
  const selectedLabels = labelIds ?? defaultLabelIds;

  const labelOf = (id) => byId.get(id)?.label ?? "";
  const suggestedName = defaultQueueName(mode, {
    labelA: labelOf(runA),
    labelB: labelOf(runB),
    label: labelOf(rubricRun),
  });
  const queueName = nameTouched ? name : suggestedName;

  const queryClient = useQueryClient();

  const rowCount = {
    selected: exampleIds.length,
    filter: filterQ.data?.total,
    all: allQ.data?.total,
    pending,
  }[effectiveRows];

  const validate = () => {
    if (isPair && (!runA || !runB)) return "Pick two runs";
    if (isPair && runA === runB) return "Pick two different runs";
    if (!isPair && !rubricRun) return "Pick a run";
    if (queueValue === "new" && !queueName.trim()) return "Name the queue";
    if (
      queueValue === "new" &&
      selectedLabels.length === 0 &&
      !(isPair && !pairLabel)
    )
      return "Select at least one label";
    if (queueValue === "existing" && !target) return "Pick a queue";
    if (effectiveRows !== "pending" && rowCount === 0)
      return "These runs share no rows";
    if (queueValue === "new") return outsideProblem(outside, rowCount);
    return null;
  };

  const resolveIds = async () => {
    if (effectiveRows === "pending") return [];
    const base = { runs: viewRuns, baseline, sort };
    let ids;
    if (effectiveRows === "selected") {
      const sorted = await fetchResultExampleIds(datasetId, {
        ...base,
        filters: [],
      });
      const pick = new Set(exampleIds);
      ids = sorted.example_ids.filter((id) => pick.has(id));
    } else {
      const res = await fetchResultExampleIds(datasetId, {
        ...base,
        filters: effectiveRows === "filter" ? filters : [],
      });
      ids = res.example_ids;
    }
    return order === "random" ? seededShuffle(ids, queueName) : ids;
  };

  const submit = async (startReviewing) => {
    const problem = validate();
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const ids = await resolveIds();
      if (effectiveRows !== "pending" && ids.length === 0) {
        setError("These runs share no rows");
        return;
      }
      let queueId = target?.id;
      let queueTitle = target?.name;
      // Adding work to a finished or paused queue reopens it.
      if (queueValue === "existing" && target && target.status !== "active")
        await setQueueStatusQuiet(target.id, "active");
      if (queueValue === "new") {
        let labels = selectedLabels;
        if (isPair && !pairLabel && labels.length === 0) {
          const created = await axios.post(
            annotationLabelEndpoints.create,
            PAIRWISE_LABEL,
          );
          const label = created?.data?.result ?? created?.data;
          labels = label?.id ? [label.id] : [];
        }
        const queue = await createQueueQuiet({
          name: queueName.trim(),
          description: isPair
            ? `Pairwise review of ${labelOf(runB)} against ${labelOf(runA)}.`
            : `Rubric review of ${labelOf(rubricRun)}.`,
          instructions,
          assignment_strategy: "manual",
          annotations_required: Number(perItem) || 1,
          reservation_timeout_minutes: 60,
          requires_review: false,
          auto_assign: false,
          label_ids: labels,
          annotator_ids: annotators.map((a) => a.userId),
          annotator_roles: Object.fromEntries(
            annotators.map((a) => [a.userId, a.roles || [a.role]]),
          ),
          dataset: datasetId,
          metadata: queueMetadata({
            mode,
            runA,
            runB,
            runId: rubricRun,
            blind,
            datasetId,
            corrections,
          }),
        });
        queueId = queue?.id;
        queueTitle = queue?.name ?? queueName;
        if (queueId) await setQueueStatusQuiet(queueId, "active");
      }
      if (!queueId) throw new Error("Queue not created");
      let added = 0;
      if (ids.length) {
        const result = await addQueueItemsQuiet(
          queueId,
          reviewItems(ids, {
            mode,
            runA,
            runB,
            runId: rubricRun,
            blind: isPair && blind,
            seed: queueTitle,
          }),
        );
        added = Number(result?.added) || 0;
      }
      if (queueValue === "new")
        await sendOutsideInvites(queueId, outside, { metric });
      refreshQueues(queryClient, datasetId);
      if (startReviewing) {
        const itemId = await fetchNextReviewItemId(queueId);
        onClose?.();
        navigate(
          reviewPageUrl(datasetId, queueId, {
            itemId,
            from: returnTo ?? `${location.pathname}${location.search}`,
          }),
        );
        // One toast for the whole action (RW#3).
        notify(
          queueValue === "new"
            ? `Queue “${queueTitle}” created · ${ids.length} ${ids.length === 1 ? "row" : "rows"}. Review starts at row 1.`
            : `Reviewing “${queueTitle}”${added ? ` · ${added} ${added === 1 ? "row" : "rows"} added` : ""}`,
          { variant: "success", key: "start-review" },
        );
      } else {
        onClose?.();
        const openQueue = queueId;
        notify(
          ids.length
            ? `Added ${added} ${added === 1 ? "row" : "rows"} to “${queueTitle}”${ids.length - added ? ` (${ids.length - added} already in it)` : ""}`
            : `“${queueTitle}” is ready`,
          {
            variant: "success",
            key: "start-review",
            action: () => (
              <Button
                size="small"
                color="inherit"
                onClick={() =>
                  navigate(
                    reviewPageUrl(datasetId, openQueue, {
                      from:
                        returnTo ?? `${location.pathname}${location.search}`,
                    }),
                  )
                }
              >
                Open queue
              </Button>
            ),
          },
        );
      }
    } catch (e) {
      setError(
        e?.message && e.message !== "Queue not created"
          ? "Could not start the review. Try again."
          : "Could not create the queue.",
      );
    } finally {
      setBusy(false);
    }
  };

  const runOptions = runs.map((r) => (
    <MenuItem key={r.run_id} value={r.run_id}>
      <Typography typography="s1" noWrap>
        {runTitle(r)}
      </Typography>
    </MenuItem>
  ));

  return (
    <Dialog
      open={Boolean(open)}
      onClose={busy ? undefined : onClose}
      PaperProps={{ sx: { width: 560, maxWidth: "calc(100vw - 32px)", p: 0 } }}
    >
      <DialogTitle
        sx={{ display: "flex", alignItems: "center", px: 2.5, pt: 2, pb: 1 }}
      >
        <Typography
          typography="m3"
          fontWeight="fontWeightSemiBold"
          sx={{ flex: 1 }}
        >
          Start a review
        </Typography>
        <IconButton
          size="small"
          aria-label="Close"
          onClick={onClose}
          disabled={busy}
        >
          <Iconify icon="mdi:close" width={18} />
        </IconButton>
      </DialogTitle>
      <DialogContent sx={{ px: 2.5, pb: 1 }}>
        <Stack spacing={2} sx={{ pt: 0.5 }}>
          <Field label="Review type">
            <ToggleButtonGroup
              exclusive
              size="small"
              value={mode}
              onChange={(_, v) => {
                if (!v) return;
                setMode(v);
                setLabelIds(null);
                setBlind(v === "pairwise");
                setQueueMode(null);
                setRows(null);
              }}
              aria-label="Review type"
            >
              <ToggleButton value="pairwise" sx={{ px: 2, typography: "s1" }}>
                Pairwise
              </ToggleButton>
              <ToggleButton value="rubric" sx={{ px: 2, typography: "s1" }}>
                Rubric
              </ToggleButton>
            </ToggleButtonGroup>
          </Field>

          {isPair ? (
            <Stack direction="row" gap={1.5}>
              <TextField
                select
                size="small"
                fullWidth
                label="Run A (baseline run)"
                value={byId.has(runA) ? runA : ""}
                onChange={(e) => {
                  setRunA(e.target.value);
                  setQueueMode(null);
                  setRows(null);
                }}
              >
                {runOptions}
              </TextField>
              <TextField
                select
                size="small"
                fullWidth
                label="Run B"
                value={byId.has(runB) ? runB : ""}
                onChange={(e) => {
                  setRunB(e.target.value);
                  setQueueMode(null);
                  setRows(null);
                }}
              >
                {runOptions}
              </TextField>
            </Stack>
          ) : (
            <TextField
              select
              size="small"
              fullWidth
              label="Run"
              value={byId.has(rubricRun) ? rubricRun : ""}
              onChange={(e) => {
                setRubricRun(e.target.value);
                setQueueMode(null);
                setRows(null);
              }}
            >
              {runOptions}
            </TextField>
          )}

          <Field label="Rows">
            <RadioGroup
              value={effectiveRows}
              onChange={(e) => setRows(e.target.value)}
            >
              {exampleIds.length ? (
                <FormControlLabel
                  value="selected"
                  control={<Radio size="small" />}
                  label={
                    <Typography typography="s1">
                      Selected{count(exampleIds.length)}
                    </Typography>
                  }
                />
              ) : null}
              {filters.length ? (
                <FormControlLabel
                  value="filter"
                  control={<Radio size="small" />}
                  label={
                    <Typography typography="s1">
                      Current filter{count(filterQ.data?.total)}
                    </Typography>
                  }
                />
              ) : null}
              <FormControlLabel
                value="all"
                control={<Radio size="small" />}
                label={
                  <Typography typography="s1">
                    All rows{count(allQ.data?.total)}
                  </Typography>
                }
              />
              {target && queueValue === "existing" && pending > 0 ? (
                <FormControlLabel
                  value="pending"
                  control={<Radio size="small" />}
                  label={
                    <Typography typography="s1">
                      Not yet reviewed in “{target.name}”{count(pending)}
                    </Typography>
                  }
                />
              ) : null}
            </RadioGroup>
          </Field>

          <Stack direction="row" gap={1.5} alignItems="flex-start">
            <TextField
              select
              size="small"
              label="Order"
              value={order}
              onChange={(e) => setOrder(e.target.value)}
              sx={{ width: 200, flexShrink: 0 }}
              helperText={
                order === "regressions" && metric
                  ? `${isPair ? "By" : "Lowest first:"} ${summaryQ.data?.metrics?.find((m) => m.key === metric)?.name ?? metric.replace(/^[a-z]+:/, "").replace(/_/g, " ")} on ${labelOf(isPair ? runB : rubricRun)}`
                  : order === "random"
                    ? "Shuffled by the queue name"
                    : "Row 1 first"
              }
            >
              {ORDERS.map((o) => (
                <MenuItem key={o.value} value={o.value}>
                  {o.label}
                </MenuItem>
              ))}
            </TextField>
            {isPair ? (
              <FormControlLabel
                sx={{ mt: 0.25 }}
                control={
                  <Checkbox
                    size="small"
                    checked={blind}
                    onChange={(e) => setBlind(e.target.checked)}
                  />
                }
                label={
                  <Typography typography="s2">
                    Blind review — hide run names, scores and cost; shuffle
                    sides
                  </Typography>
                }
              />
            ) : null}
          </Stack>

          <Field label="Queue">
            <RadioGroup
              value={queueValue}
              onChange={(e) => {
                setQueueMode(e.target.value);
                setRows(null);
              }}
            >
              <FormControlLabel
                value="existing"
                disabled={!matching.length}
                control={<Radio size="small" />}
                label={
                  <Typography
                    typography="s1"
                    color={matching.length ? "text.primary" : "text.disabled"}
                  >
                    Add to existing
                    {matching.length
                      ? ""
                      : " — no queue reviews these runs yet"}
                  </Typography>
                }
              />
              {queueValue === "existing" && matching.length ? (
                <TextField
                  select
                  size="small"
                  value={target?.id ?? ""}
                  onChange={(e) => {
                    setExistingId(e.target.value);
                    setRows(null);
                  }}
                  sx={{ ml: 3.5, mb: 1 }}
                >
                  {matching.map((q) => (
                    <MenuItem key={q.id} value={q.id}>
                      {q.name} · {q.completed_count ?? 0}/{q.item_count ?? 0}{" "}
                      done
                    </MenuItem>
                  ))}
                </TextField>
              ) : null}
              <FormControlLabel
                value="new"
                control={<Radio size="small" />}
                label={<Typography typography="s1">New queue</Typography>}
              />
            </RadioGroup>
            {queueValue === "new" ? (
              <Stack
                spacing={2}
                sx={{
                  ml: 3.5,
                  p: 1.5,
                  borderRadius: 0.5,
                  border: "1px solid",
                  borderColor: "divider",
                }}
              >
                <TextField
                  size="small"
                  label="Queue Name"
                  value={queueName}
                  onChange={(e) => {
                    setNameTouched(true);
                    setName(e.target.value);
                  }}
                  fullWidth
                />
                <Box>
                  <Typography
                    typography="s2"
                    fontWeight="fontWeightMedium"
                    sx={{ mb: 0.75 }}
                  >
                    Annotation Labels
                  </Typography>
                  {isPair && !pairLabel && !labelsQ.isLoading ? (
                    <Typography
                      typography="s2"
                      color="text.secondary"
                      sx={{ mb: 1 }}
                    >
                      “Overall preference” (A / Tie / B) is created with the
                      queue.
                    </Typography>
                  ) : null}
                  <LabelPicker
                    selectedIds={selectedLabels}
                    onChange={(ids) => setLabelIds(ids)}
                  />
                </Box>
                <CorrectionsOption
                  datasetId={datasetId}
                  value={corrections}
                  onChange={setCorrections}
                />
                <Box>
                  <Typography
                    typography="s2"
                    fontWeight="fontWeightMedium"
                    sx={{ mb: 0.75 }}
                  >
                    Annotators
                  </Typography>
                  <AnnotatorPicker
                    value={annotators}
                    onChange={setAnnotators}
                    creatorId={String(user?.id ?? "")}
                    isManager
                  />
                </Box>
                <TextField
                  size="small"
                  type="number"
                  label="Submissions per item"
                  value={perItem}
                  onChange={(e) =>
                    setPerItem(
                      e.target.value === ""
                        ? ""
                        : Math.max(1, parseInt(e.target.value, 10) || 1),
                    )
                  }
                  inputProps={{ min: 1, max: 10 }}
                  sx={{ width: 200 }}
                />
                <TextField
                  size="small"
                  label="Instructions"
                  multiline
                  minRows={2}
                  placeholder={
                    isPair
                      ? "Pick the reply you would send. Tie if both are equally good."
                      : "Rate the reply as sent to the customer."
                  }
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                  fullWidth
                />
                <OutsideReviewersStart
                  value={outside}
                  onChange={setOutside}
                  datasetId={datasetId}
                  runs={viewRuns}
                  baseline={baseline}
                  mode={mode}
                  names={{
                    mode,
                    labelA: labelOf(runA),
                    labelB: labelOf(runB),
                    label: labelOf(rubricRun),
                  }}
                  metric={metric}
                  rowFilters={effectiveRows === "filter" ? filters : []}
                  selectedIds={effectiveRows === "selected" ? exampleIds : null}
                  total={rowCount}
                />
              </Stack>
            ) : null}
          </Field>

          {error ? (
            <Alert severity="error" sx={{ py: 0.25 }}>
              {error}
            </Alert>
          ) : null}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 2.5, py: 1.75, gap: 1 }}>
        <Typography typography="s2" color="text.secondary" sx={{ flex: 1 }}>
          {effectiveRows === "pending"
            ? `${pending ?? 0} rows waiting`
            : Number.isFinite(rowCount)
              ? `${rowCount} ${rowCount === 1 ? "row" : "rows"}`
              : ""}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          onClick={onClose}
          disabled={busy}
        >
          Cancel
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="primary"
          onClick={() => submit(false)}
          disabled={busy}
        >
          Add to queue
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={() => submit(true)}
          disabled={busy}
          startIcon={
            busy ? <CircularProgress size={12} color="inherit" /> : null
          }
        >
          Start reviewing
        </Button>
      </DialogActions>
    </Dialog>
  );
};

StartReviewDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  datasetId: PropTypes.string,
  mode: PropTypes.oneOf(["pairwise", "rubric"]),
  runA: PropTypes.string,
  runB: PropTypes.string,
  // example ids to review (the current selection or filtered set)
  exampleIds: PropTypes.arrayOf(PropTypes.string),
  // URL to return to from the review page (`from=`)
  returnTo: PropTypes.string,
  // optional (additive): the view's results filters, for `Current filter (n)`
  filters: PropTypes.array,
};

export default StartReviewDialog;
