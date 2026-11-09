// W7 A1: a composite's jury on one run (GET …/composite/jury/?run_id=):
// per child pass rate and n, pairwise agreement and κ between seats (the one
// agreement computation), and `Judges disagree on N of M rows` with Open
// rows (the results grid filtered to exactly those rows, pill `judges
// disagree`). Read from the stored cells, never re-scored. Each pair's
// agreement shows its Wilson 95% range, and a low-sample note when the pair
// has fewer rows than LOW_SAMPLE_N (review SF10).
import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import useDeepLinkScroll from "./useDeepLinkScroll";
import {
  Box,
  Button,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { resultsGridHref } from "src/sections/common/Analysis/analysisLinks";
import { rowsPill } from "src/sections/common/Results/filterPills";
import { LOW_SAMPLE_N } from "src/sections/common/Metrics/metricFormat";

const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
const k2 = (v) => (v == null ? "–" : Number(v).toFixed(2));
/** `95% (75–99%)`: an agreement with its Wilson 95% range, when it has one. */
const agreementWithRange = (p) =>
  p?.agreement == null
    ? "–"
    : Array.isArray(p.ci) && p.ci[0] != null && p.ci[1] != null
      ? `${pct(p.agreement)} (${Math.round(p.ci[0] * 100)}–${Math.round(p.ci[1] * 100)}%)`
      : pct(p.agreement);

/** Whether the jury route is registered (integration) and the launch ships it (L6). */
const juryAvailable = () =>
  Boolean(endpoints.develop?.w7?.compositeJury) && isSurfaceShown("evals.jury");

export default function CompositeJuryReport({
  templateId,
  initialRunId = null,
}) {
  // `?run=<run_id>` (the drawer's Judge agreement link) picks the run.
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [runId, setRunId] = useState(initialRunId ?? params.get("run"));
  const { data, isLoading } = useQuery({
    queryKey: ["w7-composite-jury", templateId, runId],
    queryFn: async () => {
      const { data: res } = await axios.get(
        endpoints.develop.w7.compositeJury(templateId),
        { params: runId ? { run_id: runId } : {} },
      );
      return res?.result ?? res;
    },
    enabled: Boolean(templateId) && juryAvailable(),
  });
  // `?view=jury` (the results drawer's link) brings the report into view.
  const boxRef = useRef(null);
  useDeepLinkScroll(boxRef, params.get("view") === "jury" && Boolean(data));
  if (!juryAvailable() || !templateId) return null;
  if (isLoading || !data) return null;
  if (!data.runs?.length)
    return (
      <Box ref={boxRef} data-testid="composite-jury" sx={{ py: 1 }}>
        <Typography typography="s2" fontWeight={600}>
          Judge agreement
        </Typography>
        <Typography typography="s3" color="text.secondary">
          No run has scored this composite yet.
        </Typography>
      </Box>
    );
  const href = data.split_rows.length
    ? resultsGridHref({
        mode: "experiment",
        experimentId: data.experiment_id,
        datasetId: data.dataset_id,
        runIds: [data.run_id],
        pills: [rowsPill(data.split_rows, { label: "judges disagree" })],
      })
    : null;
  return (
    <Box ref={boxRef} data-testid="composite-jury" sx={{ py: 1 }}>
      <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 1 }}>
        <Typography typography="s1" fontWeight={600}>
          Judge agreement
        </Typography>
        <Select
          size="small"
          value={data.run_id ?? ""}
          onChange={(e) => setRunId(e.target.value)}
          sx={{ fontSize: "12px", height: 30 }}
          inputProps={{ "aria-label": "Run" }}
        >
          {data.runs.map((r) => (
            <MenuItem key={r.run_id} value={r.run_id} sx={{ fontSize: "12px" }}>
              {r.run_label}
            </MenuItem>
          ))}
        </Select>
      </Stack>
      <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 1 }}>
        <Typography typography="s2" data-testid="jury-split-line">
          {`Judges disagree on ${data.split_rows.length} of ${data.n} rows`}
        </Typography>
        {href ? (
          <Button
            size="small"
            variant="outlined"
            onClick={() => navigate(href)}
            sx={{ textTransform: "none" }}
          >
            Open rows
          </Button>
        ) : null}
      </Stack>
      <Table size="small" data-testid="jury-children">
        <TableHead>
          <TableRow>
            <TableCell>Child</TableCell>
            <TableCell>Judge</TableCell>
            <TableCell align="right">Pass rate</TableCell>
            <TableCell align="right">n</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {data.children.map((c) => (
            <TableRow key={c.id}>
              <TableCell>{c.name}</TableCell>
              <TableCell>{c.judge_model ?? "–"}</TableCell>
              <TableCell align="right">{pct(c.pass_rate)}</TableCell>
              <TableCell align="right">{c.n}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {data.pairs.length ? (
        <Table size="small" sx={{ mt: 1.5 }} data-testid="jury-pairs">
          <TableHead>
            <TableRow>
              <TableCell>Pair</TableCell>
              <TableCell align="right">Agreement (95% range)</TableCell>
              <TableCell align="right">κ</TableCell>
              <TableCell align="right">n</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {data.pairs.map((p) => {
              const nameOf = (id) => {
                const c = data.children.find((x) => x.id === id);
                return c?.judge_model
                  ? `${c.name} (${c.judge_model})`
                  : c?.name ?? id;
              };
              return (
                <TableRow key={`${p.a}|${p.b}`}>
                  <TableCell>{`${nameOf(p.a)} · ${nameOf(p.b)}`}</TableCell>
                  <TableCell align="right">{agreementWithRange(p)}</TableCell>
                  <TableCell align="right">{k2(p.kappa)}</TableCell>
                  <TableCell align="right">{p.n}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      ) : null}
      {data.pairs.some((p) => p.low_sample) ? (
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ mt: 0.75, display: "block" }}
          data-testid="jury-low-sample"
        >
          {`Low sample: fewer than ${LOW_SAMPLE_N} rows, so each 95% range is wide.`}
        </Typography>
      ) : null}
    </Box>
  );
}

CompositeJuryReport.propTypes = {
  templateId: PropTypes.string,
  initialRunId: PropTypes.string,
};
