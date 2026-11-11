import PropTypes from "prop-types";
import { useId, useState } from "react";
import { Box, Button, Collapse, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  advancedSamplingSummary,
  isSimpleSamplingPolicy,
  pickNames,
  rowNames,
  samplingLocalDate,
  simpleSelectionText,
} from "./simpleSamplingPolicy";

// Module-local: a component file exports only components (fast refresh).
const samplingFieldLabel = ({ source = "span", key = "" } = {}) =>
  `${{ span: "Span attribute", metadata: "Metadata", resource: "Resource attribute" }[source] || source}: ${key}`;
const unitIdentityText = (policy) =>
  [
    policy.sampling_unit === "attribute"
      ? `Custom identity: ${policy.unit_attribute?.key ? samplingFieldLabel(policy.unit_attribute) : "the project’s contact attribute"}.`
      : `Pick ${pickNames(policy.sampling_unit)} together.`,
    policy.missing_id === "row"
      ? "Rows without an ID are sampled individually."
      : "Rows without an ID are excluded.",
  ].join(" ");

// A quiet text toggle for secondary detail, in place of a native <details>.
export function SamplingDisclosure({ label, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <Box sx={{ minWidth: 0 }}>
      <Button
        size="small"
        color="inherit"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
        endIcon={
          <Iconify
            icon={
              open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
            }
            width={14}
          />
        }
        sx={{
          minWidth: 0,
          px: 0.5,
          ml: -0.5,
          typography: "s1",
          color: "text.secondary",
          "& .MuiButton-endIcon": { ml: 0.5 },
        }}
      >
        {label}
      </Button>
      <Collapse in={open} id={id}>
        <Box sx={{ pt: 0.5 }}>{children}</Box>
      </Collapse>
    </Box>
  );
}
SamplingDisclosure.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
  defaultOpen: PropTypes.bool,
};

export function SamplingWindow({ start, end, ongoing = false, children }) {
  return (
    <Box sx={{ minWidth: 0, overflowWrap: "anywhere" }}>
      <Typography variant="s1" component="div" color="text.secondary">
        {ongoing ? "New incoming data from " : "Fixed window: "}
        {samplingLocalDate(start)}
        {/* The format names the time zone itself. */}
        {!ongoing && ` – ${samplingLocalDate(end)}`}
      </Typography>
      {children || (
        <SamplingDisclosure label="Exact UTC window">
          <Typography variant="s3" component="div" color="text.secondary">
            Start: {start || "Not set"}; end:{" "}
            {ongoing ? "Open-ended" : end || "Not set"}
          </Typography>
        </SamplingDisclosure>
      )}
    </Box>
  );
}
SamplingWindow.propTypes = {
  start: PropTypes.string,
  end: PropTypes.string,
  ongoing: PropTypes.bool,
  // Replaces the read-only exact-window disclosure, e.g. with the editor's
  // own UTC inputs, so the same window is never shown twice.
  children: PropTypes.node,
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
      <Typography
        variant="s1"
        component="div"
        sx={{ overflowWrap: "anywhere" }}
      >
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
        <Typography variant="s1" component="div" color="text.secondary">
          {unitIdentityText(policy)}
        </Typography>
      )}
      {policy?.mode === "closed" && task.run_type === "continuous" && (
        <Typography variant="s1" color="text.secondary" component="div">
          {`Keeps evaluating late ${rows} from the picked ${pickNames(policy.sampling_unit)}; no new ${pickNames(policy.sampling_unit)} are added after the sample freezes.`}
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
