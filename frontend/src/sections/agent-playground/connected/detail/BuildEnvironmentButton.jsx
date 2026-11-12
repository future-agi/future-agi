import PropTypes from "prop-types";
import React, { useState } from "react";
import { Button } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import BuildEnvironmentDialog from "./BuildEnvironmentDialog";

export default function BuildEnvironmentButton({
  agent,
  canEdit,
  primary = false,
}) {
  const [open, setOpen] = useState(false);
  const isCode = agent?.agentKind === "source";
  const reason = !agent
    ? ""
    : !isCode
      ? "Only a code agent can be built into an environment here"
      : !canEdit
        ? "You don't have permission to build an environment."
        : "";
  const button = (
    <Button
      size="small"
      variant={primary ? "contained" : "outlined"}
      color={primary ? "primary" : "inherit"}
      disabled={!agent || !!reason}
      onClick={() => setOpen(true)}
    >
      Build environment
    </Button>
  );
  return (
    <>
      {reason ? (
        <CustomTooltip show title={reason} size="small" arrow>
          <span tabIndex={0} aria-label={reason}>
            {button}
          </span>
        </CustomTooltip>
      ) : (
        button
      )}
      {open && agent && (
        <BuildEnvironmentDialog
          agent={agent}
          open
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

BuildEnvironmentButton.propTypes = {
  agent: PropTypes.object,
  canEdit: PropTypes.bool,
  primary: PropTypes.bool,
};
