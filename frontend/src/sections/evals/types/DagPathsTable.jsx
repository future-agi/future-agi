// W7 A2 (W7-3): the paths a run's cells took through a decision tree (GET
// …/dag/paths/?run_id=): path, rows, share, mean score and Open rows (the
// results grid filtered to exactly those rows, pill `path = …`). A run
// picker lists the runs this tree scored. Read from the stored cells.
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
import { resultsGridHref } from "src/sections/common/Analysis/analysisLinks";
import { rowsPill } from "src/sections/common/Results/filterPills";

const pct = (v) => `${Math.round((v ?? 0) * 100)}%`;

export default function DagPathsTable({ templateId }) {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [runId, setRunId] = useState(params.get("run"));
  const { data } = useQuery({
    queryKey: ["w7-dag-paths", templateId, runId],
    queryFn: async () => {
      const { data: res } = await axios.get(
        endpoints.develop.w7.dagPaths(templateId),
        {
          params: runId ? { run_id: runId } : {},
        },
      );
      return res?.result ?? res;
    },
    enabled: Boolean(templateId && endpoints.develop?.w7?.dagPaths),
  });
  // `?view=paths` (the results drawer's link) brings the report into view.
  const boxRef = useRef(null);
  useDeepLinkScroll(boxRef, params.get("view") === "paths" && Boolean(data));
  if (!data) return null;
  if (!data.runs?.length)
    return (
      <Box ref={boxRef} data-testid="dag-paths" sx={{ py: 1 }}>
        <Typography typography="s1" fontWeight={600}>
          Paths on a run
        </Typography>
        <Typography typography="s3" color="text.secondary">
          No run has scored this decision tree yet.
        </Typography>
      </Box>
    );
  return (
    <Box ref={boxRef} data-testid="dag-paths" sx={{ py: 1 }}>
      <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 1 }}>
        <Typography typography="s1" fontWeight={600}>
          Paths on a run
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
        <Typography
          typography="s3"
          color="text.secondary"
        >{`${data.n} cells`}</Typography>
      </Stack>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Path</TableCell>
            <TableCell align="right">Rows</TableCell>
            <TableCell align="right">Share</TableCell>
            <TableCell align="right">Mean score</TableCell>
            <TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {data.paths.map((p) => (
            <TableRow
              key={p.path_key}
              data-testid="dag-path-row"
              data-path-key={p.path_key}
            >
              <TableCell>{p.label}</TableCell>
              <TableCell align="right" data-testid="dag-path-n">
                {p.n}
              </TableCell>
              <TableCell align="right">{pct(p.share)}</TableCell>
              <TableCell align="right">
                {p.mean == null ? "–" : p.mean.toFixed(2)}
              </TableCell>
              <TableCell align="right">
                {data.experiment_id ? (
                  <Button
                    size="small"
                    variant="outlined"
                    sx={{ textTransform: "none" }}
                    onClick={() =>
                      navigate(
                        resultsGridHref({
                          mode: "experiment",
                          experimentId: data.experiment_id,
                          datasetId: data.dataset_id,
                          runIds: [data.run_id],
                          pills: [
                            rowsPill(p.rows, { label: `path = ${p.label}` }),
                          ],
                        }),
                      )
                    }
                  >
                    Open rows
                  </Button>
                ) : null}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

DagPathsTable.propTypes = { templateId: PropTypes.string };
