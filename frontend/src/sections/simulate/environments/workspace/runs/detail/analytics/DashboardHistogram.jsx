import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { Bars, format, NoMeasurement } from "./DashboardCharts";

export default function DashboardHistogram({ data, kind }) {
  if (!data) return <NoMeasurement />;
  const csat = kind === "csat";
  return (
    <>
      {data.measured ? (
        <Bars
          rows={data.bins}
          xKey="label"
          histogram
          axisLabel={csat ? "CSAT score" : "Average response time per call"}
          series={[{ key: "count", label: "Calls" }]}
        />
      ) : (
        <NoMeasurement />
      )}
      <Box
        sx={{ px: 2, py: 1.5, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Typography sx={{ fontSize: 11 }}>
          {csat
            ? data.agreement?.percent == null
              ? "Read: Provider/evaluation agreement is not measured — both verdicts are required."
              : `Read: The provider's own success judgement (its analysis) agrees with your evals on ${format(data.agreement.percent, "percent")} of comparable calls (${data.agreement.agreed}/${data.agreement.compared}).`
            : data.measured
              ? `Read: ${format(data.at_or_above_target_percent, "percent")} of measured calls were at or over the ${format(data.target_ms, "ms")} target. p50 ${format(data.p50, "ms")}, p95 ${format(data.p95, "ms")}.`
              : "Read: No agent response-time measurements were recorded."}
        </Typography>
        <Typography sx={{ mt: 0.5, fontSize: 10, color: "text.secondary" }}>
          {data.measured} / {data.total} calls measured.
          {csat
            ? " Fractional scores are rounded to the nearest score; missing/out-of-range scores are excluded."
            : " Bucket boundaries and threshold statistics are computed by the server."}
        </Typography>
      </Box>
    </>
  );
}
DashboardHistogram.propTypes = {
  data: PropTypes.object,
  kind: PropTypes.oneOf(["csat", "response_time"]).isRequired,
};
