// W5-9 B9 · Best of N on one row (integration B-H6: the result drawer mounts
// it). It reads the newest Best of N result on this dataset that covers
// this row and only runs in view (or the one the URL names, `bestof`), and
// shows that row's verdict, the recorded order the outputs were judged in,
// each run's score and the judge's reason. Nothing is scored here: the
// numbers are the stored result of w5BestOf.js (GET …/best-of/?id=).
// Absent until the best-of endpoint is registered, and when no result
// covers the row.
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Box, Stack, Typography } from "@mui/material";
import axios from "src/utils/axios";
import { w5Path } from "src/sections/common/Analysis/workbench/w5Api";
import {
  judgedOrder,
  judgedVsStored,
  orderText,
  scoreText,
  storedNote,
  verdictText,
} from "./bestOfModel";

const pickResult = (history, runIds, pinned, exampleId) => {
  const inView = new Set(runIds);
  const fits = (h) =>
    h.status === "completed" &&
    h.run_ids.every((id) => inView.has(id)) &&
    (!h.row_ids?.length || h.row_ids.includes(exampleId));
  if (pinned && history.some((h) => h.id === pinned && fits(h))) return pinned;
  return history.find(fits)?.id ?? null;
};

export default function BestOfCard({ datasetId, exampleId, runs }) {
  const [sp] = useSearchParams();
  const path = w5Path("bestOf", datasetId);
  const runIds = runs.map((r) => r.run_id);
  const labelOf = (id) => runs.find((r) => r.run_id === id)?.label ?? id;
  const historyQ = useQuery({
    queryKey: ["w5-best-of", datasetId, "history", runIds.join(",")],
    queryFn: () => axios.get(path, { params: { runs: runIds.join(",") } }),
    select: (res) => res?.data?.result?.history ?? [],
    enabled: Boolean(path && datasetId && runIds.length >= 2),
  });
  const id = useMemo(
    () => pickResult(historyQ.data ?? [], runIds, sp.get("bestof"), exampleId),
    [historyQ.data, runIds, sp, exampleId],
  );
  const recQ = useQuery({
    queryKey: ["w5-best-of", datasetId, id],
    queryFn: () => axios.get(path, { params: { id } }),
    select: (res) => res?.data?.result,
    enabled: Boolean(path && id),
  });
  const rec = recQ.data;
  const row = rec?.rows?.find((r) => r.example_id === exampleId);
  if (!path || !rec || !row) return null;
  const winner = row.winner && row.winner !== "tie" ? row.winner : null;
  return (
    <Box
      data-testid="best-of-card"
      data-id={rec.id}
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        px: 1.5,
        py: 1,
        bgcolor: "background.paper",
      }}
    >
      <Stack direction="row" gap={1} alignItems="baseline" flexWrap="wrap">
        <Typography typography="s1" fontWeight={600}>
          Best of {rec.run_ids.length}
        </Typography>
        <Typography typography="s2" color="text.secondary">
          {`${rec.eval_name} · ${
            rec.mode === "judge"
              ? rec.judge_model
                ? `judged by ${rec.judge_model}`
                : "scored by the check"
              : "highest stored score"
          }`}
        </Typography>
      </Stack>
      <Typography
        typography="s1"
        sx={{ mt: 0.5 }}
        data-testid="best-of-card-verdict"
      >
        {winner
          ? `Winner ${verdictText(row, labelOf)}`
          : verdictText(row, labelOf)}
      </Typography>
      {judgedOrder(rec, row).length > 0 && (
        <Typography
          typography="s3"
          color="text.secondary"
          data-testid="best-of-card-order"
        >
          {`Judged in this order: ${orderText(judgedOrder(rec, row), labelOf)}`}
        </Typography>
      )}
      {row.differs?.length > 0 && (
        <Typography
          typography="s3"
          color="warning.dark"
          sx={{ mt: 0.5 }}
          data-testid="best-of-card-stored-note"
        >
          {storedNote({
            ...rec,
            stored_differs: row.differs.length,
            stored_compared: Object.keys(row.stored ?? {}).length,
          })}
        </Typography>
      )}
      <Stack gap={0.25} sx={{ mt: 0.75 }}>
        {(rec.mode === "judge" && row.order
          ? row.order
          : Object.keys(row.scores ?? {})
        ).map((rid) => (
          <Stack
            key={rid}
            direction="row"
            gap={1}
            data-testid="best-of-card-score"
            data-run={rid}
            data-value={row.scores?.[rid] ?? ""}
          >
            <Typography
              typography="s2"
              fontWeight={rid === winner ? 600 : 400}
              sx={{ minWidth: 48 }}
            >
              {labelOf(rid)}
            </Typography>
            <Typography
              typography="s2"
              sx={{ fontVariantNumeric: "tabular-nums", minWidth: 48 }}
            >
              {scoreText(row.scores?.[rid], rec.output_type)}
            </Typography>
            {judgedVsStored(row, rid, rec.output_type) && (
              <Typography
                typography="s3"
                color="warning.dark"
                noWrap
                data-testid="best-of-card-stored"
              >
                {judgedVsStored(row, rid, rec.output_type)}
              </Typography>
            )}
            {row.reasons?.[rid] && (
              <Typography typography="s3" color="text.secondary" noWrap>
                {row.reasons[rid]}
              </Typography>
            )}
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}

BestOfCard.propTypes = {
  datasetId: PropTypes.string.isRequired,
  exampleId: PropTypes.string,
  runs: PropTypes.array.isRequired,
};
