import React from "react";
import PropTypes from "prop-types";
import Button from "@mui/material/Button";
import { useNavigate } from "react-router-dom";

import StatePanel from "../components/StatePanel";
import { sandboxPath } from "../lib/links";

// What each tab says about a deleted sandbox. Deleting stops and removes the
// container and its /workspace (EE's destroy); the record and its Activity
// stay, so every panel points there instead of promising files or a resume.
const COPY = {
  files: {
    icon: "folder",
    description:
      "Its files were removed with it. Activity lists file writes made through the Files API.",
  },
  run: {
    icon: "terminal",
    description:
      "Nothing can run in it any more. Activity lists the commands that ran, with their exit codes.",
  },
};

/** The panel a tab shows for a sandbox that was deleted. */
export default function DeletedPanel({ sandbox, what }) {
  const navigate = useNavigate();
  const copy = COPY[what];
  return (
    <StatePanel
      compact
      icon={copy.icon}
      testId="sandbox-deleted-panel"
      title="This sandbox was deleted"
      description={copy.description}
      action={
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={() => navigate(sandboxPath(sandbox.id, "activity"))}
          data-testid="sandbox-deleted-activity"
        >
          Open Activity
        </Button>
      }
    />
  );
}

DeletedPanel.propTypes = {
  sandbox: PropTypes.shape({ id: PropTypes.string.isRequired }).isRequired,
  what: PropTypes.oneOf(Object.keys(COPY)).isRequired,
};
