// W7-4 remote scorers: Score a run with this scorer. Pick a dataset and a
// settled run (optionally some rows); every call is made first, then the run
// is scored as a re-score child (the run keeps its scores). The confirm's
// call count is the job's own dry pass (GET ?for=score-run-calls).
import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router";
import { useSnackbar } from "notistack";
import {
  Alert,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  LinearProgress,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import { scoreRunConfirmText } from "src/sections/common/EvalTypes/remoteModel";
import { experimentUrl } from "src/sections/develop-detail/ExperimentTab/leaderboardModel";
import { plural, scoreRunToast } from "./remoteScorerForm";

export default function ScoreRunDialog({
  open,
  templateId,
  name,
  host,
  onClose,
}) {
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const [options, setOptions] = useState(null);
  const [datasetId, setDatasetId] = useState("");
  const [runId, setRunId] = useState("");
  const [some, setSome] = useState(false);
  const [picked, setPicked] = useState([]);
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const timer = useRef(null);

  useEffect(() => {
    axios
      .get(`${endpoints.develop.w7.remote(templateId)}?for=score-run`)
      .then((res) => setOptions(res?.data?.result?.score_run_options ?? []))
      .catch((e) => setError(e?.result ?? "Couldn't list the runs"));
    return () => clearTimeout(timer.current);
  }, [templateId]);

  const runs = useMemo(
    () => options?.find((d) => d.dataset_id === datasetId)?.runs ?? [],
    [options, datasetId],
  );
  const run = runs.find((r) => r.run_id === runId) ?? null;
  const rowKey = some ? [...picked].sort().join(",") : "";
  // {calls, cells} for the run and rows picked, null while it is counted.
  const [count, setCount] = useState(null);
  useEffect(() => {
    setCount(null);
    if (!runId || (some && !rowKey)) return undefined;
    let live = true;
    axios
      .get(endpoints.develop.w7.remote(templateId), {
        params: {
          for: "score-run-calls",
          run_id: runId,
          ...(rowKey ? { row_ids: rowKey } : {}),
        },
      })
      .then((res) => {
        if (live) setCount(res?.data?.result?.score_run_calls ?? null);
      })
      .catch((e) => live && setError(e?.result ?? "Couldn't count the calls"));
    return () => {
      live = false;
    };
  }, [templateId, runId, some, rowKey]);
  const calls = count?.calls ?? 0;

  const finish = (view) => {
    const r = view.result;
    enqueueSnackbar(scoreRunToast(r, name), {
      variant: r?.unchanged ? "info" : r?.failed ? "warning" : "success",
      ...(r?.child_run_id && {
        action: () => (
          <Button
            size="small"
            color="inherit"
            data-testid="score-run-open"
            onClick={() =>
              navigate(
                experimentUrl(r.dataset_id, r.experiment_id, {
                  runId: r.child_run_id,
                }),
              )
            }
          >
            Open {r.child_label}
          </Button>
        ),
      }),
    });
    onClose?.(r);
  };

  const poll = (jobId) => {
    axios
      .get(endpoints.develop.w7.remoteJob(jobId))
      .then((res) => {
        const view = res?.data?.result;
        setJob(view);
        if (view?.status === "done") finish(view);
        else if (view?.status === "failed")
          setError(view.error ?? "The job failed");
        else timer.current = setTimeout(() => poll(jobId), 400);
      })
      .catch((e) => setError(e?.result ?? "Couldn't read the job"));
  };

  const start = async () => {
    setError(null);
    try {
      const res = await axios.post(
        endpoints.develop.w7.remoteScoreRun(templateId),
        {
          run_id: runId,
          ...(some ? { row_ids: picked } : {}),
        },
      );
      const id = res?.data?.result?.job_id;
      setJob({ status: "calling", sent: 0, total: calls, failed: 0 });
      poll(id);
    } catch (e) {
      setError(e?.result ?? "Couldn't start");
    }
  };

  const busy = job && job.status !== "done" && job.status !== "failed";
  return (
    <Dialog
      open={open}
      onClose={busy ? undefined : () => onClose?.(null)}
      maxWidth="sm"
      fullWidth
      data-testid="score-run-dialog"
    >
      <DialogTitle sx={{ fontSize: 16 }}>Score a run with {name}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5} sx={{ pt: 1 }}>
          <TextField
            select
            size="small"
            label="Dataset"
            value={datasetId}
            onChange={(e) => {
              setDatasetId(e.target.value);
              setRunId("");
            }}
            inputProps={{ "data-testid": "score-run-dataset" }}
          >
            {(options ?? []).map((d) => (
              <MenuItem key={d.dataset_id} value={d.dataset_id}>
                {d.dataset_name}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label="Run"
            value={runId}
            onChange={(e) => {
              setRunId(e.target.value);
              setPicked([]);
            }}
            disabled={!datasetId}
            inputProps={{ "data-testid": "score-run-run" }}
          >
            {runs.map((r) => (
              <MenuItem
                key={r.run_id}
                value={r.run_id}
                data-testid={`score-run-option-${r.label}`}
              >
                {r.label} · {r.experiment_name} · {plural(r.rows.length, "row")}
              </MenuItem>
            ))}
          </TextField>
          {run && (
            <FormControlLabel
              control={
                <Checkbox
                  size="small"
                  checked={some}
                  onChange={(e) => setSome(e.target.checked)}
                />
              }
              label={<Typography variant="body2">Only some rows</Typography>}
            />
          )}
          {run && some && (
            <Stack sx={{ maxHeight: 180, overflow: "auto", pl: 1 }}>
              {run.rows.map((x) => (
                <FormControlLabel
                  key={x.id}
                  control={
                    <Checkbox
                      size="small"
                      checked={picked.includes(x.id)}
                      onChange={(e) =>
                        setPicked((p) =>
                          e.target.checked
                            ? [...p, x.id]
                            : p.filter((y) => y !== x.id),
                        )
                      }
                    />
                  }
                  label={<Typography variant="caption">{x.label}</Typography>}
                />
              ))}
            </Stack>
          )}
          {run && (some ? rowKey : true) && (
            <Typography variant="body2" data-testid="score-run-confirm">
              {!count
                ? "Counting the calls…"
                : count.cells
                  ? scoreRunConfirmText(calls, host)
                  : `Nothing to score: ${run.label} already has ${name}'s scores on these rows.`}
            </Typography>
          )}
          {run && (
            <Typography variant="caption" color="text.secondary">
              Every call is made first. Then {run.label} is scored as a new run;{" "}
              {run.label} keeps its scores.
            </Typography>
          )}
          {job && (
            <Stack spacing={0.5} data-testid="score-run-progress">
              <LinearProgress
                variant="determinate"
                value={job.total ? (100 * job.sent) / job.total : 0}
              />
              <Typography variant="caption">
                {job.status === "scoring" ? "Scoring the run… " : ""}Sent{" "}
                {job.sent} of {job.total}
                {job.failed ? ` · ${job.failed} failed` : ""}
              </Typography>
            </Stack>
          )}
          {error && <Alert severity="error">{String(error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => onClose?.(null)} disabled={Boolean(busy)}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={start}
          disabled={!run || !count?.cells || Boolean(busy)}
          data-testid="score-run-start"
        >
          Score {run?.label ?? "the run"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

ScoreRunDialog.propTypes = {
  open: PropTypes.bool,
  templateId: PropTypes.string.isRequired,
  name: PropTypes.string,
  host: PropTypes.string,
  onClose: PropTypes.func,
};
