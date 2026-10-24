import PropTypes from "prop-types";
import MetricStrip from "src/components/metric-strip";
import { improvementMetricsOf, minutesLabel } from "./improvementMetrics";

/*
  The three Improvements metrics, as a quiet strip above the list — the shared
  metric strip: a label, the figure, what it's over.
*/
export default function ImprovementMetricsStrip({ rows }) {
  const m = improvementMetricsOf(rows);
  return (
    <MetricStrip
      sx={{ flexShrink: 0 }}
      items={[
        {
          label: "Acceptance rate",
          value: m.acceptance ? `${m.acceptance.rate}%` : "—",
          hint: m.acceptance
            ? `${m.acceptance.n} of ${m.acceptance.of} finished improvements accepted`
            : "No improvement has finished yet",
          tooltip:
            "Finished improvements whose proposal someone accepted — shipped as a pull request, a draft or a new version.",
        },
        {
          label: "Time to a fix to review",
          value: minutesLabel(m.time.prompt ?? m.time.code),
          hint:
            m.time.prompt != null && m.time.code != null
              ? `Prompt fixes · code fixes take ${minutesLabel(m.time.code)}`
              : m.time.code != null
                ? "Median · code fixes"
                : m.time.prompt != null
                  ? "Median · prompt fixes"
                  : "No proposal yet",
          tooltip:
            "Median time from starting an improvement to a proposal ready for review, for prompt-level and code-level changes.",
        },
        {
          label: "Fix survival at 30 days",
          value: m.survival ? `${m.survival.rate}%` : "—",
          hint: m.survival
            ? `${m.survival.n} of ${m.survival.of} released fixes held for 30 days`
            : "No released fix has finished its 30-day watch",
          tooltip:
            "Released fixes that didn't come back in the 30 days after release.",
        },
      ]}
    />
  );
}
ImprovementMetricsStrip.propTypes = { rows: PropTypes.array };
