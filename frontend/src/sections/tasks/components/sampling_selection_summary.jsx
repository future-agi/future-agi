import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import {
  advancedSamplingSummary,
  isSimpleSamplingPolicy,
  pickNames,
  rowNames,
  simpleSelectionText,
} from "./simpleSamplingPolicy";

export const samplingFieldLabel = ({ source = "span", key = "" } = {}) =>
  `${{ span: "Span attribute", metadata: "Metadata", resource: "Resource attribute" }[source] || source}: ${key}`;
export const unitIdentityText = (policy) =>
  [
    policy.sampling_unit === "attribute"
      ? `Custom identity: ${policy.unit_attribute?.key ? samplingFieldLabel(policy.unit_attribute) : "the project’s contact attribute"}.`
      : `Pick ${pickNames(policy.sampling_unit)} together.`,
    policy.missing_id === "row"
      ? "Rows without an ID are sampled individually."
      : "Rows without an ID are excluded.",
  ].join(" ");
const localDate = (value) => {
  const date = new Date(value);
  return value && Number.isFinite(+date)
    ? new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        timeZoneName: "short",
      }).format(date)
    : "Not set";
};
export function SamplingWindow({ start, end, ongoing = false }) {
  return (
    <Box sx={{ minWidth: 0, overflowWrap: "anywhere" }}>
      <Typography variant="caption" color="text.secondary">
        {ongoing ? "New incoming data from " : "Fixed window: "}
        {localDate(start)}
        {!ongoing && ` – ${localDate(end)}`} (local time)
      </Typography>
      <Box
        component="details"
        sx={{
          "& > summary": {
            cursor: "pointer",
            fontSize: 12,
            color: "text.secondary",
          },
        }}
      >
        <summary>Exact UTC window</summary>
        <Typography variant="caption" color="text.secondary">
          Start: {start || "Not set"}; end:{" "}
          {ongoing ? "Open-ended" : end || "Not set"}
        </Typography>
      </Box>
    </Box>
  );
}
SamplingWindow.propTypes = {
  start: PropTypes.string,
  end: PropTypes.string,
  ongoing: PropTypes.bool,
};

export default function SavedTaskSelection({ task }) {
  // Read persisted evidence, never the editable form or date-preset normalization.
  const policy = task.sampling_policy;
  const rows = rowNames(task.row_type);
  const start =
    task.filters_applied?.start_date || task.filters_applied?.date_range?.[0];
  const end =
    task.filters_applied?.end_date || task.filters_applied?.date_range?.[1];
  return (
    <Box aria-label="Saved data selection" sx={{ minWidth: 0 }}>
      <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>
        Saved selection ·{" "}
        {policy
          ? isSimpleSamplingPolicy(policy)
            ? simpleSelectionText(policy, task.row_type)
            : `Pick ${pickNames(policy.sampling_unit)}; evaluate matching ${rows}. ${advancedSamplingSummary(policy)}`
          : `${Number(task.sampling_rate) === 100 ? "All" : `About ${task.sampling_rate}% of`} filter-matching ${rows}.${task.run_type === "historical" ? ` Process up to ${Number(task.spans_limit).toLocaleString()} matching rows.` : ""}`}{" "}
        {policy?.strata?.length > 0 &&
          !isSimpleSamplingPolicy(policy) &&
          `${policy.strata.length} saved group rules. `}
        {task.run_type === "continuous"
          ? "New incoming data."
          : "Historical data."}
      </Typography>
      {policy?.sampling_unit === "attribute" && (
        <Typography variant="caption" color="text.secondary">
          {unitIdentityText(policy)}
        </Typography>
      )}
      {policy?.mode === "closed" && task.run_type === "continuous" && (
        <Typography variant="caption" color="text.secondary" component="div">
          Continue evaluating late rows from picked groups; new groups are not
          admitted after membership freezes.
        </Typography>
      )}
      {(policy || task.run_type === "historical") && (
        <SamplingWindow
          start={policy?.window.start || start}
          end={policy?.window.end || end}
          ongoing={policy?.mode === "live"}
        />
      )}
    </Box>
  );
}
SavedTaskSelection.propTypes = { task: PropTypes.object.isRequired };
