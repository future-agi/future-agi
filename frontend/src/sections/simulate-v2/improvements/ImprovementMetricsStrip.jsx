import PropTypes from "prop-types";
import MetricStrip from "src/components/metric-strip";
import { improvementMetricsOf, minutesLabel } from "./improvementMetrics";

/*
  The three Improvements metrics, as a quiet strip above the list — the shared
  metric strip, dense: one line each with a label, the figure, what it's over.
*/
export default function ImprovementMetricsStrip({ rows }) {
  const m = improvementMetricsOf(rows);
  return (
    <MetricStrip
      dense
      sx={{ flexShrink: 0 }}
      items={[
        {
          label: "Acceptance",
          value: m.acceptance ? `${m.acceptance.rate}%` : "—",
          hint: m.acceptance
            ? `${m.acceptance.n} of ${m.acceptance.of} epochs`
            : "none finished",
          tooltip:
            "Finished epochs whose proposal someone accepted — shipped as a pull request, a draft or a new version.",
        },
        {
          label: "Time to review",
          value: minutesLabel(m.time.prompt ?? m.time.code),
          hint:
            m.time.prompt != null && m.time.code != null
              ? `prompt · code ${minutesLabel(m.time.code)}`
              : m.time.code != null
                ? "code"
                : m.time.prompt != null
                  ? "prompt"
                  : "none yet",
          tooltip:
            "Median time from starting an epoch to a proposal ready for review, for prompt-level and code-level changes.",
        },
        {
          label: "30-day survival",
          value: m.survival ? `${m.survival.rate}%` : "—",
          hint: m.survival
            ? `${m.survival.n} of ${m.survival.of} changes`
            : "none yet",
          tooltip:
            "Released changes whose issue didn't come back in the 30 days after release.",
        },
      ]}
    />
  );
}
ImprovementMetricsStrip.propTypes = { rows: PropTypes.array };
