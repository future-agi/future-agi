import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { ALK_MONO } from "../../alkTokens";
import Fold from "./Fold";
import { metricLabel, splitMetrics } from "./metrics";

/** Everything ALK measures is 0..1, so one shape reads for all of them. */
const toneOf = (value) => {
  if (value >= 0.8) return "accent.pass";
  if (value >= 0.5) return "accent.tool";
  return "accent.fail";
};

const MetricBar = ({ metric }) => {
  const value = Number(metric.score) || 0;
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <Box
      // The label column is fixed so the eye compares bars down a column instead of
      // parsing seventeen numbers.
      sx={{
        display: "grid",
        gridTemplateColumns: "13rem 1fr 3rem",
        alignItems: "center",
        gap: 2.4,
        my: 0.35,
      }}
      title={metric.reason || undefined}
    >
      <Box component="span" sx={{ fontSize: 12.5, opacity: 0.8, color: "text.primary" }}>
        {metricLabel(metric.name)}
      </Box>
      <Box
        data-testid="metric-track"
        sx={{ height: 8, borderRadius: 1, overflow: "hidden", bgcolor: "action.hover" }}
      >
        <Box sx={{ height: "100%", width: `${pct}%`, borderRadius: 1, bgcolor: toneOf(value) }} />
      </Box>
      <Box
        component="span"
        sx={{
          fontSize: 12.5,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
          color: "text.secondary",
        }}
      >
        {value.toFixed(2)}
      </Box>
    </Box>
  );
};

MetricBar.propTypes = { metric: PropTypes.object.isRequired };

const Note = ({ children }) => (
  <Typography sx={{ fontFamily: ALK_MONO, fontSize: 11.8, color: "text.secondary", my: 0.2, ml: 1 }}>
    {children}
  </Typography>
);

Note.propTypes = { children: PropTypes.node };

/** The suite-wide block and the per-scenario one are the same thing at two scopes. */
const MetricBars = ({ metrics, title }) => {
  const { measured, clean, absent } = splitMetrics(metrics);
  if (!measured.length && !clean.length && !absent.length) return null;

  return (
    <Box sx={{ mt: 1 }}>
      {title && (
        <Typography
          sx={{
            fontFamily: ALK_MONO,
            fontSize: 10.6,
            letterSpacing: "0.07em",
            textTransform: "uppercase",
            color: "text.secondary",
            mb: 0.5,
          }}
        >
          {title}
        </Typography>
      )}

      {measured.length > 0
        ? measured.map((metric) => <MetricBar key={metric.name} metric={metric} />)
        : clean.length > 0 && (
            <Typography variant="body2" color="text.secondary">
              nothing scored below 1.00
            </Typography>
          )}

      {clean.length > 0 && (
        <Fold label={`${clean.length} checks ran and found nothing`}>
          {clean.map((metric) => (
            <Note key={metric.name}>{`${metricLabel(metric.name)} — ${metric.reason || "clean"}`}</Note>
          ))}
        </Fold>
      )}

      {absent.length > 0 && (
        <Fold label={`${absent.length} did not apply to this run`}>
          <Typography variant="body2" color="text.secondary">
            These score 1.00 because there was nothing to measure, so they are not results.
          </Typography>
          {absent.map((metric) => (
            <Note key={metric.name}>
              {`${metricLabel(metric.name)} — ${metric.reason || "not applicable"}`}
            </Note>
          ))}
        </Fold>
      )}
    </Box>
  );
};

MetricBars.propTypes = {
  metrics: PropTypes.oneOfType([PropTypes.array, PropTypes.object]),
  title: PropTypes.string,
};

export default MetricBars;
