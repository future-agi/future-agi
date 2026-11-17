// Print or save as PDF (W5-5 B8, AG-03 / E15): the Summary as one printable
// page, from the same R5 analysis and R1 runs the Summary reads
// (printModel.js). An overlay over the app; the print CSS hides everything
// else and sets A4 landscape, so the browser's print dialog saves the PDF.
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  GlobalStyles,
  Portal,
  Stack,
  Typography,
} from "@mui/material";
import { printModel } from "./printModel";

const cellSx = {
  border: "1px solid #d0d5dd",
  px: 1,
  py: 0.5,
  fontSize: 12,
  fontVariantNumeric: "tabular-nums",
  verticalAlign: "top",
};

const PRINT_CSS = {
  "@page": { size: "A4 landscape", margin: "12mm" },
  "@media print": {
    "body *": { visibility: "hidden !important" },
    "[data-print-root], [data-print-root] *": {
      visibility: "visible !important",
    },
    "[data-print-root]": {
      position: "absolute !important",
      inset: "0 auto auto 0 !important",
      width: "100% !important",
      overflow: "visible !important",
      boxShadow: "none !important",
    },
    "[data-print-hide]": { display: "none !important" },
  },
};

const SummaryPrintView = ({
  datasetName,
  analysis,
  runs,
  baselineId,
  link,
  onClose,
}) => {
  const model = useMemo(
    () => printModel({ analysis, runs, baselineId }),
    [analysis, runs, baselineId],
  );
  const date = new Date().toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  const title = `${datasetName} · ${model.runs.map((r) => r.label).join(" vs ")}`;
  return (
    <Portal>
      <Box
        data-print-root
        data-testid="summary-print"
        sx={{
          position: "fixed",
          inset: 0,
          zIndex: 2000,
          bgcolor: "#fff",
          color: "#101828",
          overflowY: "auto",
          p: 3,
        }}
      >
        <GlobalStyles styles={PRINT_CSS} />
        <Stack
          direction="row"
          gap={1}
          justifyContent="flex-end"
          data-print-hide
          sx={{ mb: 1 }}
        >
          <Button
            variant="contained"
            size="small"
            onClick={() => window.print()}
          >
            Print or save as PDF
          </Button>
          <Button size="small" onClick={onClose}>
            Close
          </Button>
        </Stack>
        <Typography sx={{ fontSize: 20, fontWeight: 700 }}>{title}</Typography>
        <Typography sx={{ fontSize: 12, color: "#475467" }}>
          {date}
          {model.baselineLabel
            ? ` · baseline ${model.baselineLabel}`
            : ""} · {link}
        </Typography>

        {model.verdicts.length > 0 && (
          <Box sx={{ mt: 2 }}>
            <Typography sx={{ fontSize: 14, fontWeight: 700, mb: 0.5 }}>
              Verdict
            </Typography>
            {model.verdicts.map((v) => (
              <Typography
                key={v.runId}
                data-testid="print-verdict"
                data-reason={v.verdict == null ? "1" : "0"}
                sx={{ fontSize: 12, mb: 0.25 }}
              >
                <b>{v.label}</b> ·{" "}
                <Box
                  component="span"
                  sx={{ color: v.verdict == null ? "#475467" : "inherit" }}
                >
                  {v.sentence}
                </Box>
              </Typography>
            ))}
          </Box>
        )}

        <Typography sx={{ fontSize: 14, fontWeight: 700, mt: 2, mb: 0.5 }}>
          Metrics
        </Typography>
        <Box
          component="table"
          sx={{ borderCollapse: "collapse", width: "100%" }}
        >
          <thead>
            <tr>
              <Box component="th" sx={{ ...cellSx, textAlign: "left" }}>
                Metric
              </Box>
              {model.runs.map((r) => (
                <Box
                  component="th"
                  key={r.id}
                  sx={{ ...cellSx, textAlign: "right" }}
                >
                  {r.label} {r.name ?? ""}
                </Box>
              ))}
            </tr>
          </thead>
          <tbody>
            {model.metrics.map((m) => (
              <tr key={m.key}>
                <Box component="td" sx={cellSx}>
                  {m.name}
                </Box>
                {m.cells.map((c) => (
                  <Box
                    component="td"
                    key={c.runId}
                    data-testid="print-metric"
                    data-metric={m.name}
                    data-run={c.runId}
                    data-value={c.value ?? ""}
                    sx={{
                      ...cellSx,
                      textAlign: "right",
                      bgcolor:
                        m.best === c.runId
                          ? "#ecfdf3"
                          : m.worst === c.runId
                            ? "#fef3f2"
                            : "transparent",
                      color:
                        m.best === c.runId
                          ? "#067647"
                          : m.worst === c.runId
                            ? "#b42318"
                            : "inherit",
                      WebkitPrintColorAdjust: "exact",
                      printColorAdjust: "exact",
                    }}
                  >
                    {c.text}
                    {c.ci ? (
                      <Box
                        component="div"
                        sx={{ fontSize: 10, color: "#667085" }}
                      >
                        {c.ci} · n {c.n}
                      </Box>
                    ) : null}
                  </Box>
                ))}
              </tr>
            ))}
          </tbody>
        </Box>
        <Typography sx={{ fontSize: 10, color: "#667085", mt: 0.5 }}>
          Green = best, red = worst per metric · under each value its 95%
          interval (Wilson for rates, ± for means) and n.
        </Typography>

        {model.configDiffs.length > 0 && (
          <Box sx={{ mt: 2 }}>
            <Typography sx={{ fontSize: 14, fontWeight: 700, mb: 0.5 }}>
              Config differences vs {model.baselineLabel}
            </Typography>
            {model.configDiffs.map((d) => (
              <Typography key={d.label} sx={{ fontSize: 12 }}>
                <b>{d.label}</b> ·{" "}
                {d.chips.length ? d.chips.join(" · ") : "same settings"}
              </Typography>
            ))}
          </Box>
        )}

        {model.worst.length > 0 && (
          <Box sx={{ mt: 2, breakInside: "avoid" }}>
            <Typography sx={{ fontSize: 14, fontWeight: 700, mb: 0.5 }}>
              Worst {model.worst.length} rows
              {model.worklistRun ? ` (${model.worklistRun})` : ""}
            </Typography>
            <Box
              component="table"
              sx={{ borderCollapse: "collapse", width: "100%" }}
            >
              <tbody>
                {model.worst.map((w) => (
                  <tr key={w.id} data-testid="print-worst-row">
                    <Box component="td" sx={{ ...cellSx, width: 60 }}>
                      Row {w.row}
                    </Box>
                    <Box component="td" sx={cellSx}>
                      {w.input.length > 160
                        ? `${w.input.slice(0, 159)}…`
                        : w.input}
                    </Box>
                    <Box component="td" sx={cellSx}>
                      {w.regressed}
                    </Box>
                  </tr>
                ))}
              </tbody>
            </Box>
          </Box>
        )}
      </Box>
    </Portal>
  );
};

SummaryPrintView.propTypes = {
  datasetName: PropTypes.string,
  analysis: PropTypes.object,
  runs: PropTypes.array,
  baselineId: PropTypes.string,
  link: PropTypes.string,
  onClose: PropTypes.func.isRequired,
};

export default SummaryPrintView;
