import React from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import {
  rowLimitsExtras,
  rowLimitsLine,
} from "src/sections/common/RunSettings/rowLimitsModel";
import { runScopeBody } from "./runScopeBody";

export default function ScopePolicySummary({ values }) {
  const policy = runScopeBody({
    error_policy: values.errorPolicy ?? "zero",
    prompt_config: (values.promptConfig ?? []).map(rowLimitsExtras),
  });
  const line = rowLimitsLine(policy.limits);
  return (
    <Stack spacing={0.5}>
      <Typography typography="s2">
        {policy.error_policy === "exclude"
          ? "Errors excluded from scores"
          : "Errors count as 0 in scores"}
      </Typography>
      {line ? <Typography typography="s2">{line}</Typography> : null}
      <Typography typography="s3" color="text.secondary">
        Per-row limits apply to every run. Use identical limits on all run
        cards. Counts shown before creation are requested counts; the server
        confirms the saved selection after creation.
      </Typography>
      {policy.limits?.messages ? (
        <Typography typography="s3" color="text.secondary">
          Messages count every input message plus the reserved response on each
          provider request, including repeated history in later requests.
        </Typography>
      ) : null}
      {policy.limits?.timeout_s ? (
        <Typography typography="s3" color="text.secondary">
          Time budget is shared across the row. Provider calls use its remaining
          time; non-provider graph work is checked between nodes, not
          interrupted.
        </Typography>
      ) : null}
    </Stack>
  );
}
ScopePolicySummary.propTypes = { values: PropTypes.object.isRequired };
