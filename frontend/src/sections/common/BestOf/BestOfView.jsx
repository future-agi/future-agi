// Explore › Best of N (W5-9 B9, EV-16): pick 2–8 runs and either a judge
// eval or `Highest score on <eval>`; the cost line comes from the one
// estimator; Run posts to best-of and the result (URL key `bestof`, so a
// reload keeps it) shows each run's win share with its 95% interval and
// every row's recorded order, scores and winner or tie. Computed only in
// the mock's w5BestOf.js (the one scorer for judge mode).
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Button,
  Checkbox,
  FormControlLabel,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import axios from "src/utils/axios";
import { apiErrorText, w5Path } from "../Analysis/workbench/w5Api";
import {
  BEST_OF_MAX_RUNS,
  BEST_OF_MIN_RUNS,
  costLine,
  pickOptions,
  pickValueOf,
  rowsOf,
  storedNote,
  summaryLine,
} from "./bestOfModel";
import { RowsTable, Shares } from "./BestOfRows";

const ROWS_SHOWN = 60;
const NUM = { fontVariantNumeric: "tabular-nums" };

const BestOfView = ({ datasetId, runs, keepIds, runColors, onOpenRows }) => {
  const [sp, setSp] = useSearchParams();
  const resultId = sp.get("bestof");
  const allIds = runs.map((r) => r.run_id);
  const [picked, setPicked] = useState(() => allIds.slice(0, BEST_OF_MAX_RUNS));
  const [choice, setChoice] = useState("");
  const [filter, setFilter] = useState("all");
  const [showAll, setShowAll] = useState(false);
  const rowIds = keepIds ? [...keepIds] : null;
  const labelOf = (id) => runs.find((r) => r.run_id === id)?.label ?? id;
  const colorOf = (id) => runColors?.[id]?.solid ?? "primary.main";
  const path = w5Path("bestOf", datasetId);

  const optionsQ = useQuery({
    queryKey: ["w5", "best-of", datasetId, picked.join(","), rowIds?.length],
    queryFn: () =>
      axios.get(path, {
        params: {
          runs: picked.join(","),
          ...(rowIds ? { row_ids: rowIds.join(",") } : {}),
        },
      }),
    select: (res) => res?.data?.result,
    enabled: Boolean(path) && picked.length >= BEST_OF_MIN_RUNS,
  });
  const options = useMemo(
    () => pickOptions(optionsQ.data?.evals ?? []),
    [optionsQ.data],
  );
  useEffect(() => {
    if (options.length && !options.some((o) => o.value === choice))
      setChoice(options[0].value);
  }, [options, choice]);
  const opt = options.find((o) => o.value === choice);
  const evalOpt = optionsQ.data?.evals?.find((e) => e.key === opt?.key);

  const resultQ = useQuery({
    queryKey: ["w5", "best-of-result", datasetId, resultId],
    queryFn: () => axios.get(path, { params: { id: resultId } }),
    select: (res) => res?.data?.result,
    enabled: Boolean(path && resultId),
  });
  const run = useMutation({
    mutationFn: () =>
      axios.post(path, {
        run_ids: picked,
        eval_id: opt.key,
        mode: opt.mode,
        ...(rowIds ? { row_ids: rowIds } : {}),
      }),
    onSuccess: (res) => {
      setFilter("all");
      setSp(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("bestof", res?.data?.result?.id);
          return next;
        },
        { replace: true },
      );
    },
  });
  const rec = resultQ.data;
  // A result opened from the link (`bestof`) puts its own eval and mode in
  // the picker, so "Run again" reruns what is shown (M7). A later pick by
  // the user is kept.
  const [syncedId, setSyncedId] = useState(null);
  useEffect(() => {
    const value = pickValueOf(rec);
    if (!rec?.id || rec.id === syncedId || !value) return;
    if (!options.some((o) => o.value === value)) return;
    setChoice(value);
    setSyncedId(rec.id);
  }, [rec, options, syncedId]);
  if (!path) return null;
  const toggle = (id) =>
    setPicked((p) =>
      p.includes(id)
        ? p.filter((x) => x !== id)
        : p.length < BEST_OF_MAX_RUNS
          ? allIds.filter((x) => p.includes(x) || x === id)
          : p,
    );
  const shown = rec ? rowsOf(rec.rows, filter) : [];
  const recLabel = (id) => labelOf(id);

  return (
    <Stack gap={2} sx={{ p: 0.5 }} data-testid="best-of">
      <Stack gap={1}>
        <Typography typography="s1" fontWeight={600}>
          Best of N runs
        </Typography>
        <Typography typography="s2" color="text.secondary">
          Compares the score each output already has on the eval you pick: the
          highest stored score wins a row and equal top scores are a tie.
          Nothing is judged again.
        </Typography>
        <Stack direction="row" flexWrap="wrap" gap={0.5} alignItems="center">
          <Typography typography="s2" sx={{ mr: 1 }}>
            Runs
          </Typography>
          {runs.map((r) => (
            <FormControlLabel
              key={r.run_id}
              control={
                <Checkbox
                  size="small"
                  checked={picked.includes(r.run_id)}
                  onChange={() => toggle(r.run_id)}
                  inputProps={{ "aria-label": `Include ${r.label}` }}
                />
              }
              label={r.label}
            />
          ))}
          <Typography typography="s3" color="text.secondary">
            {`${picked.length} of ${BEST_OF_MIN_RUNS}–${BEST_OF_MAX_RUNS}`}
          </Typography>
        </Stack>
        <Stack direction="row" gap={1.5} alignItems="center" flexWrap="wrap">
          <TextField
            select
            size="small"
            label="Pick the best by"
            value={opt ? choice : ""}
            onChange={(e) => setChoice(e.target.value)}
            inputProps={{ "aria-label": "Pick the best by" }}
            sx={{ minWidth: 280 }}
            disabled={!options.length}
          >
            {options.map((o) => (
              <MenuItem key={o.value} value={o.value}>
                {o.label}
              </MenuItem>
            ))}
          </TextField>
          {opt && optionsQ.data && (
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="best-of-cost"
              sx={NUM}
            >
              {costLine({
                mode: opt.mode,
                evalOpt,
                rows: optionsQ.data.rows,
                outputs: optionsQ.data.outputs,
              })}
            </Typography>
          )}
          <Button
            variant="contained"
            size="small"
            disabled={!opt || picked.length < BEST_OF_MIN_RUNS || run.isPending}
            onClick={() => run.mutate()}
          >
            {run.isPending ? "Running…" : rec ? "Run again" : "Run"}
          </Button>
        </Stack>
        {picked.length < BEST_OF_MIN_RUNS && (
          <Typography typography="s2" color="text.secondary">
            Pick at least two runs.
          </Typography>
        )}
        {run.isError && (
          <Typography typography="s2" color="error.main">
            {apiErrorText(run.error)}
          </Typography>
        )}
      </Stack>
      {rec && (
        <Stack gap={1.5} data-testid="best-of-result" data-id={rec.id}>
          <Typography typography="s1" fontWeight={600}>
            {`${rec.mode === "judge" ? "Judged" : "Highest score"} on ${rec.eval_name}`}
          </Typography>
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="best-of-summary"
          >
            {summaryLine(rec, recLabel)}
          </Typography>
          {storedNote(rec) && (
            <Typography
              typography="s2"
              color="warning.dark"
              data-testid="best-of-stored-note"
              data-differs={rec.stored_differs}
              data-compared={rec.stored_compared}
            >
              {storedNote(rec)}
            </Typography>
          )}
          <Shares
            rec={rec}
            labelOf={recLabel}
            colorOf={colorOf}
            filter={filter}
            onFilter={setFilter}
          />
          <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
            {[
              ["all", `All ${rec.rows.length}`],
              ["tie", `Ties ${rec.ties}`],
              ...(rec.skipped ? [["skipped", `Skipped ${rec.skipped}`]] : []),
            ].map(([k, l]) => (
              <Button
                key={k}
                size="small"
                variant={filter === k ? "contained" : "outlined"}
                onClick={() => setFilter(k)}
              >
                {l}
              </Button>
            ))}
            <Button
              size="small"
              disabled={!shown.length}
              onClick={() =>
                onOpenRows(
                  shown.map((r) => r.example_id),
                  `Best of N · ${filter === "all" ? "all rows" : filter === "tie" ? "ties" : filter === "skipped" ? "skipped" : `${recLabel(filter)} wins`}`,
                )
              }
            >
              {`Open these ${shown.length} rows`}
            </Button>
          </Stack>
          <RowsTable
            rows={showAll ? shown : shown.slice(0, ROWS_SHOWN)}
            runIds={rec.run_ids}
            labelOf={recLabel}
            outputType={rec.output_type}
            showOrder={rec.mode === "judge"}
            onOpenRow={(r) =>
              onOpenRows([r.example_id], `Best of N · Row ${r.row_order}`)
            }
          />
          {shown.length > ROWS_SHOWN && !showAll && (
            <Button
              size="small"
              onClick={() => setShowAll(true)}
              sx={{ alignSelf: "flex-start" }}
            >
              {`Show all ${shown.length} rows`}
            </Button>
          )}
        </Stack>
      )}
    </Stack>
  );
};

BestOfView.propTypes = {
  datasetId: PropTypes.string.isRequired,
  runs: PropTypes.array.isRequired,
  keepIds: PropTypes.object,
  runColors: PropTypes.object,
  onOpenRows: PropTypes.func.isRequired,
};

export default BestOfView;
