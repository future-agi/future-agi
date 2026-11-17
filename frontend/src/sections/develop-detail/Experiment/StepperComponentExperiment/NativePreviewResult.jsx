import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";

// Display native values and outcomes verbatim; live card chips never score them.
export default function NativePreviewResult({ result }) {
  const { assessment, diagnostics } = result.metadata;
  const output = result.output;
  const display =
    output != null && typeof output === "object"
      ? JSON.stringify(output)
      : String(output ?? "Unavailable");
  return (
    <Stack data-testid="native-preview-result">
      <Typography data-testid="eval-test-score">{display}</Typography>
      <Typography typography="s3" color="text.secondary">
        {assessment.status}
        {assessment.reason ? ` · ${assessment.reason}` : ""}
      </Typography>
      {diagnostics?.reason === "evaluation_log_unavailable" && (
        <Typography typography="s3" color="text.secondary">
          Diagnostics unavailable: no evaluation log.
        </Typography>
      )}
    </Stack>
  );
}
NativePreviewResult.propTypes = { result: PropTypes.object.isRequired };
