import PropTypes from "prop-types";
import { Button } from "@mui/material";

import Iconify from "src/components/iconify";

import useCopyForCodingAgent from "./useCopyForCodingAgent";

// A run as a prompt for the person's own coding agent, with "Copied" for a moment
// after. The caller shows it only while coding-agent attempts are on.
export default function CopyForCodingAgentButton({
  sourceRunId,
  variant = "outlined",
  ...other
}) {
  const { copy, copied, busy } = useCopyForCodingAgent(sourceRunId);
  return (
    <Button
      size="small"
      variant={variant}
      color="primary"
      disabled={busy}
      onClick={copy}
      startIcon={
        <Iconify
          icon={
            copied ? "solar:check-circle-linear" : "solar:clipboard-text-linear"
          }
          width={14}
        />
      }
      {...other}
    >
      {copied ? "Copied" : "Copy for coding agent"}
    </Button>
  );
}

CopyForCodingAgentButton.propTypes = {
  sourceRunId: PropTypes.string.isRequired,
  variant: PropTypes.oneOf(["outlined", "contained", "text"]),
};
