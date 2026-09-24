import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";

import { BUILD_TONES } from "../buildTones";
import { READ_AUDIT_COPY } from "../readAudit.constants";
import SectionHead from "./SectionHead";
import SectionIssue from "./SectionIssue";

// Icons per check verdict: a failure is a blocker, a skipped check just did not
// run (usually because an earlier one failed).
const CHECK_ICON = {
  failed: "solar:close-circle-linear",
  skipped: "solar:minus-circle-linear",
};

// The backend's own verdicts, rendered one card per check so simultaneous
// failures all stay visible. `detail`, the missing aliases and `fix` are the
// only actionable text the preflight returns, so all three go on the card.
function checkToIssue(check) {
  const hint = [
    check.detail,
    check.missing?.length
      ? `${READ_AUDIT_COPY.checkMissing}: ${check.missing.join(", ")}`
      : null,
    check.fix ? `${READ_AUDIT_COPY.checkFix}: ${check.fix}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return {
    severity: "warning",
    icon: CHECK_ICON[check.status] || CHECK_ICON.skipped,
    message: check.label,
    hint,
    retryLabel: null,
  };
}

export default function PreflightChecks({ checks }) {
  const failing = (checks || []).filter((check) => check.status !== "passed");
  if (!failing.length) return null;

  return (
    <Box sx={{ mb: 2.5 }}>
      <SectionHead
        icon="solar:shield-warning-linear"
        title={READ_AUDIT_COPY.checksTitle}
        subtitle={READ_AUDIT_COPY.checksSubtitle}
        count={failing.length}
        accent={BUILD_TONES.amber}
      />
      <Stack spacing={0.5}>
        {failing.map((check) => (
          <SectionIssue key={check.id} issue={checkToIssue(check)} />
        ))}
      </Stack>
    </Box>
  );
}

PreflightChecks.propTypes = {
  checks: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      label: PropTypes.string,
      status: PropTypes.string,
      detail: PropTypes.string,
      missing: PropTypes.arrayOf(PropTypes.string),
      fix: PropTypes.string,
    }),
  ),
};
