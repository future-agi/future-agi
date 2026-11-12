// W5-4 B3 · the Summary Overview's Classification card (integration B-H3: the
// Overview mounts it). Per run in view: n, accuracy with its 95% interval,
// macro F1 and the most frequent confusion, from GET …/classification/?runs=
// (w5Classification.js over resultsRows); `Open in Explore` opens the
// Classification view. Absent until the endpoint is registered and while
// the dataset has no classification setup.
import React from "react";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import { Link as RouterLink, useLocation } from "react-router-dom";
import { Box, Button, Stack, Typography } from "@mui/material";
import axios from "src/utils/axios";
import { w5Path } from "src/sections/common/Analysis/workbench/w5Api";
import {
  CLASSIFICATION_KEY,
  accuracyText,
  cellLabel,
  f2,
  setupLine,
  topConfusion,
} from "./classificationModel";

const cell = { px: 1, py: 0.5, borderBottom: 1, borderColor: "divider" };
const NUM = { ...cell, fontVariantNumeric: "tabular-nums", textAlign: "right" };

export default function ClassificationCard({ datasetId, runs }) {
  const { pathname, search } = useLocation();
  const path = w5Path("classification", datasetId);
  const runIds = runs.map((r) => r.run_id);
  const q = useQuery({
    queryKey: [CLASSIFICATION_KEY, datasetId, runIds.join(",")],
    queryFn: () => axios.get(path, { params: { runs: runIds.join(",") } }),
    select: (res) => res?.data?.result,
    enabled: Boolean(path && datasetId && runIds.length),
  });
  const d = q.data;
  const scored = runIds.filter((id) => d?.runs?.[id]?.n > 0);
  if (!path || !d?.setup || !scored.length) return null;
  const labelOf = (id) => runs.find((r) => r.run_id === id)?.label ?? id;
  const explore = (() => {
    const sp = new URLSearchParams(search);
    sp.set("view", "workbench");
    sp.set("wbtab", "classification");
    return `${pathname}?${sp.toString()}`;
  })();
  return (
    <Box
      data-testid="classification-card"
      sx={{
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        px: 2,
        py: 1.5,
      }}
    >
      <Stack direction="row" alignItems="baseline" gap={1.5} flexWrap="wrap">
        <Typography typography="s1" fontWeight={600}>
          Classification
        </Typography>
        <Typography typography="s2" color="text.secondary">
          {setupLine(d.setup, d.columns ?? [])}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Button size="small" component={RouterLink} to={explore}>
          Open in Explore
        </Button>
      </Stack>
      <Box
        component="table"
        sx={{ borderCollapse: "collapse", typography: "s2", mt: 1 }}
      >
        <thead>
          <tr>
            {["Run", "Rows", "Accuracy (95%)", "Macro F1", "Most confused"].map(
              (h) => (
                <Box
                  component="th"
                  key={h}
                  sx={{ ...cell, color: "text.secondary", textAlign: "left" }}
                >
                  {h}
                </Box>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {scored.map((id) => {
            const m = d.runs[id];
            const top = topConfusion(m.matrix);
            return (
              <tr
                key={id}
                data-testid="classification-card-run"
                data-run={id}
                data-accuracy={m.accuracy?.value ?? ""}
                data-macro-f1={m.macro_f1 ?? ""}
                data-n={m.n}
              >
                <Box component="td" sx={cell}>
                  {labelOf(id)}
                </Box>
                <Box component="td" sx={NUM}>
                  {m.unparsed ? `${m.n} · ${m.unparsed} unparsed` : m.n}
                </Box>
                <Box component="td" sx={NUM}>
                  {accuracyText(m.accuracy)}
                </Box>
                <Box component="td" sx={NUM}>
                  {f2(m.macro_f1)}
                </Box>
                <Box component="td" sx={{ ...cell, color: "text.secondary" }}>
                  {top ? cellLabel(top) : "—"}
                </Box>
              </tr>
            );
          })}
        </tbody>
      </Box>
    </Box>
  );
}

ClassificationCard.propTypes = {
  datasetId: PropTypes.string.isRequired,
  runs: PropTypes.array.isRequired,
};
