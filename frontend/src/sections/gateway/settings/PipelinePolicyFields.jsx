import React, { useId } from "react";
import PropTypes from "prop-types";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

// RL-917: effective node values only. Stored org mode/fail_open are not read
// by the runtime and must never be presented as the effective policy.
export default function PipelinePolicyFields({ nodePipeline }) {
  const noticeId = useId();
  // NEW: site claims review required for the value labels and notice below.
  const mode =
    typeof nodePipeline?.mode === "string" && nodePipeline.mode
      ? nodePipeline.mode === "sequential"
        ? "Sequential"
        : nodePipeline.mode
      : "Not reported";
  const failOpen =
    typeof nodePipeline?.fail_open === "boolean"
      ? nodePipeline.fail_open
        ? "On"
        : "Off"
      : "Not reported";

  return (
    <Stack spacing={1} sx={{ mb: 2 }} data-read-only="">
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
        <TextField
          size="small"
          label="Mode"
          value={mode}
          InputProps={{ readOnly: true }}
          inputProps={{ "aria-describedby": noticeId }}
        />
        <TextField
          size="small"
          label="Fail Open"
          value={failOpen}
          InputProps={{ readOnly: true }}
          inputProps={{ "aria-describedby": noticeId }}
        />
      </Stack>
      <Typography typography="s2" color="text.secondary" id={noticeId}>
        Mode and Fail Open are set by this gateway and are read-only here.
      </Typography>
      {/* NEW: site claims review required; describe only a reported policy. */}
      {typeof nodePipeline?.fail_open === "boolean" && (
        <Typography typography="s2" color="text.secondary">
          {nodePipeline.fail_open
            ? "With guardrails on, this gateway's default is to skip a check that times out or panics and continue. Failed synchronous request rewrites are still refused, and a remote-service error still follows the check's threshold and action."
            : "With guardrails on, this gateway's default is to treat check errors or timeouts as failures. A check's action can block, warn or log. Failed synchronous request rewrites are still refused."}
        </Typography>
      )}
    </Stack>
  );
}

PipelinePolicyFields.propTypes = {
  nodePipeline: PropTypes.object,
};
