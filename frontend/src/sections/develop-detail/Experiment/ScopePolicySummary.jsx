import React from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import {
  TIME_LIMIT_HELP,
  rowLimitsExtras,
  rowLimitsLine,
  unavailableRowLimitsText,
} from "src/sections/common/RunSettings/rowLimitsModel";
import { runScopeBody } from "./runScopeBody";
import { errorPolicyLine } from "./scopeReceipt";

export default function ScopePolicySummary({ values }) {
  // The same body the create sends: no policy unless the person chose one.
  const policy = runScopeBody({
    error_policy: values.errorPolicy,
    prompt_config: (values.promptConfig ?? []).map(rowLimitsExtras),
  });
  const line = rowLimitsLine(policy.limits);
  // Token, message and cost limits are refused in this release; say so here
  // too (the Configuration step blocks Run with the same line).
  const unavailable = unavailableRowLimitsText(policy.limits);
  return (
    <Stack spacing={0.5}>
      <Typography typography="s2">
        {errorPolicyLine(policy.error_policy)}
      </Typography>
      {line ? <Typography typography="s2">{line}</Typography> : null}
      <Typography typography="s3" color="text.secondary">
        Per-row limits apply to every run. Use identical limits on all run
        cards. Counts shown before creation are requested counts; the server
        confirms the saved selection after creation.
      </Typography>
      {unavailable ? (
        <Typography typography="s3" color="error.main">
          {unavailable}
        </Typography>
      ) : null}
      {policy.limits?.timeout_s ? (
        <Typography typography="s3" color="text.secondary">
          {TIME_LIMIT_HELP}
        </Typography>
      ) : null}
    </Stack>
  );
}
ScopePolicySummary.propTypes = { values: PropTypes.object.isRequired };
